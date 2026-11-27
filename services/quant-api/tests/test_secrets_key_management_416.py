"""Package 58: versioned keys, rotation, startup refusal, and redaction.

On main before this change, identity ciphertext had no key id and a repeated
32-byte master key was accepted at enrollment. These tests cover the refusal
and rotation behavior added in the control-plane secrets module.
"""

import base64
import importlib.util
import io
import json
import logging
import os
import secrets
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from osiris_api.control_plane.api import router
from osiris_api.control_plane.cli import main
from osiris_api.control_plane.database import Database
from osiris_api.control_plane.identity import totp
from osiris_api.control_plane.secrets import (
    EncryptedSecrets,
    ExternalKmsAdapter,
    KeyConfigurationError,
    SecretRedactionFilter,
    VaultSecrets,
    active_secret,
    load_key_ring,
    open_identity_store,
    parse_key_ring,
    recognize_secret,
    rotate_purpose,
    sign_evidence,
    validate_startup_keys,
    verify_evidence_signature,
)

PLAINTEXT = "PLAINTEXT-SECRET-VALUE"


def _row(purpose, key_id, version, status, material):
    algorithm = {
        "identity-master": "aes-256-gcm",
        "audit-signing": "ed25519",
        "api-token": "opaque",
        "broker-credential": "opaque",
    }[purpose]
    return {
        "purpose": purpose,
        "key_id": key_id,
        "version": version,
        "status": status,
        "algorithm": algorithm,
        "encoding": "base64",
        "material": base64.b64encode(material).decode(),
    }


def _document(rows):
    return json.dumps({"schema": "osiris.key-ring.v1", "keys": rows})


def _scanner():
    path = Path(__file__).resolve().parents[3] / "scripts" / "scan_committed_secrets.py"
    spec = importlib.util.spec_from_file_location("scan_committed_secrets_416", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_constructor_seals_without_a_key_id(tmp_path):
    db = Database(str(tmp_path / "secrets.db"))
    store = EncryptedSecrets(db, os.urandom(32))
    store.put("provider-a", PLAINTEXT)
    assert set(db.get("secrets", "provider-a")["value"]) == {"sealed"}
    assert PLAINTEXT not in (tmp_path / "secrets.db").read_bytes().decode("utf-8", "ignore")
    with pytest.raises(ValueError, match="32_byte_external_master_key_required"):
        EncryptedSecrets(db, b"short")


def test_startup_rejects_weak_short_placeholder_reused_and_unversioned_keys():
    weak = base64.b64encode(b"k" * 32).decode()
    with pytest.raises(KeyConfigurationError, match="^weak_key$") as caught:
        validate_startup_keys({
            "OSIRIS_CONTROL_MASTER_KEY": weak,
            "OSIRIS_CONTROL_MASTER_KEY_ID": "identity-master-1",
            "OSIRIS_CONTROL_MASTER_KEY_VERSION": "1",
        })
    assert weak not in str(caught.value)
    assert "k" * 8 not in str(caught.value)
    with pytest.raises(KeyConfigurationError, match="^key_id_required$"):
        validate_startup_keys({
            "OSIRIS_CONTROL_MASTER_KEY": base64.b64encode(os.urandom(32)).decode(),
        })
    with pytest.raises(KeyConfigurationError, match="^key_too_short$"):
        parse_key_ring(_document([
            _row("identity-master", "identity-master-1", 1, "active", os.urandom(16)),
        ]))
    placeholder = b"changeme-placeholder-sample-xxxx"
    assert len(placeholder) == 32
    with pytest.raises(KeyConfigurationError, match="^placeholder_key$"):
        parse_key_ring(_document([
            _row("identity-master", "identity-master-1", 1, "active", placeholder),
        ]))
    shared = os.urandom(32)
    with pytest.raises(KeyConfigurationError, match="^key_reused_across_purposes$"):
        parse_key_ring(_document([
            _row("identity-master", "identity-master-1", 1, "active", shared),
            _row("audit-signing", "audit-signing-1", 1, "active", shared),
        ]))
    with pytest.raises(KeyConfigurationError, match="^active_key_not_newest$"):
        parse_key_ring(_document([
            _row("identity-master", "identity-master-1", 1, "active", os.urandom(32)),
            _row("identity-master", "identity-master-2", 2, "retired", os.urandom(32)),
        ]))


def test_strong_legacy_key_reports_its_id_and_hides_the_bytes():
    key = os.urandom(32)
    described = validate_startup_keys({
        "OSIRIS_CONTROL_MASTER_KEY": base64.b64encode(key).decode(),
        "OSIRIS_CONTROL_MASTER_KEY_ID": "identity-master-1",
        "OSIRIS_CONTROL_MASTER_KEY_VERSION": "1",
    })
    assert described["configured"] is True
    assert described["keys"] == [{
        "purpose": "identity-master",
        "key_id": "identity-master-1",
        "version": 1,
        "status": "active",
        "algorithm": "aes-256-gcm",
    }]
    assert base64.b64encode(key).decode() not in json.dumps(described)


def test_key_file_permissions_symlink_and_source_agreement(tmp_path):
    material = os.urandom(32)
    document = _document([
        _row("identity-master", "identity-master-1", 1, "active", material),
    ])
    marker = base64.b64encode(material).decode()
    world = tmp_path / "world.json"
    world.write_text(document)
    world.chmod(0o644)
    with pytest.raises(KeyConfigurationError, match="^key_file_world_readable$") as caught:
        load_key_ring("file:" + str(world))
    assert marker not in str(caught.value)
    writable = tmp_path / "writable.json"
    writable.write_text(document)
    writable.chmod(0o602)
    with pytest.raises(KeyConfigurationError, match="^key_file_world_writable$"):
        load_key_ring("file:" + str(writable))
    grouped = tmp_path / "group.json"
    grouped.write_text(document)
    grouped.chmod(0o640)
    loaded = load_key_ring("file:" + str(grouped))
    assert loaded.active("identity-master").key_id == "identity-master-1"
    private = tmp_path / "private.json"
    private.write_text(document)
    private.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(private)
    with pytest.raises(KeyConfigurationError, match="^key_file_symlink$"):
        load_key_ring("file:" + str(link))
    from_env = load_key_ring("env:OSIRIS_KEY_RING", environ={"OSIRIS_KEY_RING": document})
    from_file = load_key_ring("file:" + str(private))
    assert from_env.active("identity-master").material == from_file.active("identity-master").material
    with pytest.raises(KeyConfigurationError, match="^ambiguous_key_source$"):
        validate_startup_keys({
            "OSIRIS_KEY_RING": document,
            "OSIRIS_KEY_RING_FILE": str(private),
        })


def test_absent_keys_do_not_fail_and_paper_mode_ignores_a_bad_ring(tmp_path):
    assert validate_startup_keys({}) == {"configured": False, "keys": []}
    bad = tmp_path / "ring.json"
    bad.write_text("{")
    bad.chmod(0o644)
    env = os.environ.copy()
    env["OSIRIS_KEY_RING_FILE"] = str(bad)
    env.pop("OSIRIS_KEY_RING", None)
    env.pop("OSIRIS_CONTROL_MASTER_KEY", None)
    env.pop("OSIRIS_ENABLE_LIVE_EXECUTION", None)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = subprocess.run(
        [sys.executable, "-c", (
            "from osiris_api.paper_execution import LIVE_EXECUTION_ENABLED, _refuse_live\n"
            "assert LIVE_EXECUTION_ENABLED is False\n"
            "_refuse_live(False)\n"
        )],
        env=env, check=False, capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    with pytest.raises(KeyConfigurationError, match="^key_file_world_readable$"):
        validate_startup_keys({"OSIRIS_KEY_RING_FILE": str(bad)})


def test_retired_master_key_decrypts_and_only_the_active_key_seals(tmp_path):
    db = Database(str(tmp_path / "secrets.db"))
    first, second = os.urandom(32), os.urandom(32)
    store = EncryptedSecrets(db, ring=parse_key_ring(_document([
        _row("identity-master", "identity-master-1", 1, "active", first),
    ])))
    store.put("provider-a", PLAINTEXT)
    saved = db.get("secrets", "provider-a")["value"]
    assert saved["key_id"] == "identity-master-1"
    assert saved["version"] == 1
    assert PLAINTEXT not in repr(store)
    rotated = parse_key_ring(_document([
        _row("identity-master", "identity-master-1", 1, "retired", first),
        _row("identity-master", "identity-master-2", 2, "active", second),
    ]))
    restarted = EncryptedSecrets(db, ring=rotated)
    assert restarted.get("provider-a") == PLAINTEXT
    restarted.put("provider-b", PLAINTEXT)
    assert db.get("secrets", "provider-b")["value"]["key_id"] == "identity-master-2"
    only_new = parse_key_ring(_document([
        _row("identity-master", "identity-master-2", 2, "active", second),
    ]))
    with pytest.raises(ValueError, match="^secret_decryption_failed$") as caught:
        EncryptedSecrets(db, ring=only_new).open("provider-a", saved)
    assert PLAINTEXT not in str(caught.value)


def test_reencrypt_moves_ciphertext_onto_the_active_key(tmp_path):
    db = Database(str(tmp_path / "secrets.db"))
    first, second = os.urandom(32), os.urandom(32)
    EncryptedSecrets(db, ring=parse_key_ring(_document([
        _row("identity-master", "identity-master-1", 1, "active", first),
    ]))).put("provider-a", PLAINTEXT)
    old = db.get("secrets", "provider-a")["value"]
    rotated = EncryptedSecrets(db, ring=parse_key_ring(_document([
        _row("identity-master", "identity-master-1", 1, "retired", first),
        _row("identity-master", "identity-master-2", 2, "active", second),
    ])))
    result = rotated.reencrypt()
    assert result["reencrypted"] == 1
    assert result["active_key_id"] == "identity-master-2"
    assert result["active_version"] == 2
    assert PLAINTEXT not in json.dumps(result)
    current = db.get("secrets", "provider-a")["value"]
    assert current["key_id"] == "identity-master-2"
    only_new = EncryptedSecrets(db, ring=parse_key_ring(_document([
        _row("identity-master", "identity-master-2", 2, "active", second),
    ])))
    assert only_new.get("provider-a") == PLAINTEXT
    with pytest.raises(ValueError, match="^secret_decryption_failed$"):
        only_new.open("provider-a", old)


def test_audit_signatures_verify_retired_keys_and_sign_with_the_active_key():
    ring = parse_key_ring(_document([
        _row("audit-signing", "audit-signing-1", 1, "active", os.urandom(32)),
    ]))
    signed = sign_evidence(ring, {"event": "checkpoint", "live_execution": False})
    assert signed["key_id"] == "audit-signing-1"
    assert signed["key_version"] == 1
    assert verify_evidence_signature(ring, signed)["live_execution"] is False
    material = base64.b64encode(ring.active("audit-signing").material).decode()
    assert material not in repr(ring)
    assert material not in json.dumps(ring.public_rows())
    rotated = rotate_purpose(ring, "audit-signing", "audit-signing-2")
    assert verify_evidence_signature(rotated, signed)["event"] == "checkpoint"
    fresh = sign_evidence(rotated, {"event": "next", "live_execution": False})
    assert fresh["key_id"] == "audit-signing-2"
    assert fresh["key_version"] == 2
    assert verify_evidence_signature(rotated, fresh)["event"] == "next"
    tampered = json.loads(json.dumps(fresh))
    tampered["bundle"]["signature"] = "A" * len(tampered["bundle"]["signature"])
    with pytest.raises(ValueError, match="evidence_signature_invalid"):
        verify_evidence_signature(rotated, tampered)
    forged = dict(signed)
    forged["key_id"] = "audit-signing-missing"
    with pytest.raises(ValueError, match="evidence_signature_invalid"):
        verify_evidence_signature(rotated, forged)


def test_opaque_secret_rotation_issues_the_active_value_and_recognizes_the_old_one():
    old = secrets.token_urlsafe(24).encode()
    new = secrets.token_urlsafe(24).encode()
    broker = secrets.token_urlsafe(24).encode()
    ring = parse_key_ring(_document([
        _row("api-token", "api-token-1", 1, "retired", old),
        _row("api-token", "api-token-2", 2, "active", new),
        _row("broker-credential", "broker-1", 1, "active", broker),
    ]))
    assert active_secret(ring, "api-token") == new.decode()
    assert active_secret(ring, "api-token") != old.decode()
    assert recognize_secret(ring, "api-token", old.decode()) is True
    assert recognize_secret(ring, "api-token", new.decode()) is True
    assert recognize_secret(ring, "api-token", "not-the-token") is False
    assert new.decode() not in repr(ring)


def test_repr_logs_and_filters_hide_secret_values(tmp_path):
    material = os.urandom(32)
    ring = parse_key_ring(_document([
        _row("identity-master", "identity-master-1", 1, "active", material),
    ]))
    encoded = base64.b64encode(material).decode()
    store = EncryptedSecrets(Database(str(tmp_path / "secrets.db")), ring=ring)
    assert encoded not in repr(ring)
    assert encoded not in repr(ring.active("identity-master"))
    assert encoded not in repr(store)
    vault = VaultSecrets("https://vault.example.invalid", "TEST-TOKEN-VALUE", object())
    assert "TEST-TOKEN-VALUE" not in repr(vault)
    redactor = SecretRedactionFilter([PLAINTEXT, material])
    assert PLAINTEXT not in repr(redactor)
    assert encoded not in repr(redactor)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("osiris.test.redaction.416")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addFilter(redactor)
    logger.info("leaked %s %s", PLAINTEXT, encoded)
    logger.info("also %s", PLAINTEXT)
    rendered = stream.getvalue()
    assert PLAINTEXT not in rendered
    assert encoded not in rendered
    assert "[redacted]" in rendered
    module_log = logging.getLogger("osiris_api.control_plane.secrets")
    captured = io.StringIO()
    attached = logging.StreamHandler(captured)
    module_log.addHandler(attached)
    module_log.setLevel(logging.INFO)
    try:
        module_log.info("ring leak %s", encoded)
        assert encoded not in captured.getvalue()
    finally:
        module_log.removeHandler(attached)


def test_disabled_kms_refuses_without_opening_a_socket(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("network")

    monkeypatch.setattr(socket, "socket", boom)
    with pytest.raises(KeyConfigurationError, match="^external_kms_disabled$"):
        ExternalKmsAdapter("https://kms.example.invalid", "not-a-real-key")


def test_keys_cli_rotates_without_printing_material(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("OSIRIS_KEY_RING", raising=False)
    monkeypatch.delenv("OSIRIS_KEY_RING_FILE", raising=False)
    monkeypatch.delenv("OSIRIS_CONTROL_MASTER_KEY", raising=False)
    first, second = tmp_path / "ring.json", tmp_path / "rotated.json"
    database = tmp_path / "control.sqlite3"
    main([
        "keys", "--action", "init", "--purpose", "identity-master",
        "--key-id", "identity-master-1", "--output", str(first),
    ])
    init_out = capsys.readouterr().out
    ring = load_key_ring("file:" + str(first))
    secret = base64.b64encode(ring.active("identity-master").material).decode()
    assert secret not in init_out
    assert stat.S_IMODE(first.stat().st_mode) & 0o077 == 0
    main([
        "keys", "--action", "rotate", "--purpose", "identity-master",
        "--key-id", "identity-master-2", "--from-ring", str(first),
        "--output", str(second),
    ])
    rotate_out = capsys.readouterr().out
    rotated = load_key_ring("file:" + str(second))
    active = base64.b64encode(rotated.active("identity-master").material).decode()
    assert secret not in rotate_out
    assert active not in rotate_out
    assert rotated.active("identity-master").key_id == "identity-master-2"
    assert any(item.status == "retired" for item in rotated.versions("identity-master"))
    assert stat.S_IMODE(second.stat().st_mode) & 0o077 == 0
    monkeypatch.setenv("OSIRIS_KEY_RING_FILE", str(second))
    open_identity_store(Database(str(database))).put("provider-a", "CLI-SECRET-VALUE")
    main(["keys", "--action", "reencrypt", "--database", str(database)])
    reencrypted = capsys.readouterr().out
    assert "CLI-SECRET-VALUE" not in reencrypted
    assert secret not in reencrypted
    assert active not in reencrypted
    assert json.loads(reencrypted)["reencrypted"] == 1


def test_other_commands_still_require_a_database(monkeypatch):
    monkeypatch.delenv("OSIRIS_CONTROL_DB", raising=False)
    with pytest.raises(SystemExit):
        main(["clock"])


def test_api_refuses_a_weak_master_key_without_echoing_it(tmp_path, monkeypatch, caplog):
    weak = base64.b64encode(b"k" * 32).decode()
    monkeypatch.setenv("OSIRIS_CONTROL_DB", str(tmp_path / "control.db"))
    monkeypatch.setenv("OSIRIS_CONTROL_MASTER_KEY", weak)
    monkeypatch.setenv("OSIRIS_CONTROL_MASTER_KEY_ID", "identity-master-1")
    monkeypatch.setenv("OSIRIS_CONTROL_MASTER_KEY_VERSION", "1")
    monkeypatch.delenv("OSIRIS_KEY_RING", raising=False)
    monkeypatch.delenv("OSIRIS_KEY_RING_FILE", raising=False)
    monkeypatch.setenv("OSIRIS_ADMIN_TOKENS", json.dumps({"analyst": "test-analyst-token"}))
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    with caplog.at_level(logging.ERROR):
        response = client.post(
            "/control-plane/auth/enroll",
            headers={"Authorization": "Bearer test-analyst-token"},
        )
    assert response.status_code == 503
    assert response.json()["detail"] == "weak_key"
    assert weak not in response.text
    assert "k" * 16 not in response.text
    assert weak not in caplog.text


def test_api_bodies_omit_the_master_key_and_the_stored_secret(tmp_path, monkeypatch):
    key = os.urandom(32)
    encoded = base64.b64encode(key).decode()
    monkeypatch.setenv("OSIRIS_CONTROL_DB", str(tmp_path / "control.db"))
    monkeypatch.setenv("OSIRIS_CONTROL_MASTER_KEY", encoded)
    monkeypatch.setenv("OSIRIS_CONTROL_MASTER_KEY_ID", "identity-master-1")
    monkeypatch.setenv("OSIRIS_CONTROL_MASTER_KEY_VERSION", "1")
    monkeypatch.delenv("OSIRIS_KEY_RING", raising=False)
    monkeypatch.delenv("OSIRIS_KEY_RING_FILE", raising=False)
    monkeypatch.setenv(
        "OSIRIS_ADMIN_TOKENS",
        json.dumps({"security_admin": "test-security_admin-token"}),
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    header = {"Authorization": "Bearer test-security_admin-token"}
    enrollment = client.post("/control-plane/auth/enroll", headers=header)
    assert enrollment.status_code == 200, enrollment.text
    assert encoded not in enrollment.text
    code = totp(enrollment.json()["secret"], int(time.time() // 30))
    login = client.post("/control-plane/auth/login", headers=header, json={"code": code})
    assert login.status_code == 200, login.text
    assert encoded not in login.text
    session = {"Authorization": "Session " + login.json()["session_token"]}
    stored = "STORED-SECRET-VALUE"
    saved = client.post(
        "/control-plane/secrets/provider-a", headers=session, json={"value": stored}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"stored": True, "version": 1}
    assert stored not in saved.text
    assert encoded not in saved.text
    rejected = client.post(
        "/control-plane/secrets/provider-b", headers=session, json={"value": ""}
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "invalid_secret_size"
    assert stored not in rejected.text


def test_secret_scan_flags_private_keys_tokens_and_dotenv_files(tmp_path):
    scanner = _scanner()
    key = tmp_path / "leak.pem"
    header = "-----BEGIN PRIVATE KEY-----"
    footer = "-----END PRIVATE KEY-----"
    key.write_text(header + "\n" + ("A" * 64) + "\n" + footer + "\n")
    env = tmp_path / ".env"
    token = "ghp_" + ("a" * 36)
    env.write_text("TOKEN=" + token + "\n")
    example = tmp_path / ".env.example"
    example.write_text("TOKEN=replace-at-runtime\n")
    findings = scanner.scan_paths([key, env, example])
    kinds = {(item["kind"], Path(item["path"]).name) for item in findings}
    assert ("private_key", "leak.pem") in kinds
    assert ("dotenv_file", ".env") in kinds
    assert ("api_token", ".env") in kinds
    assert all(Path(item["path"]).name != ".env.example" for item in findings)
    rendered = json.dumps(findings)
    assert token not in rendered
    assert "AAAA" not in rendered


def test_this_repository_has_no_committed_secrets():
    findings = _scanner().scan_repository(Path(__file__).resolve().parents[3])
    assert findings == []
