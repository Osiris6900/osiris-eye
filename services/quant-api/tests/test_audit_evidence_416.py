"""Tamper-evident audit evidence. Live trading stays disabled."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from osiris_api.audit_evidence import checkpoint_evidence, verify_evidence
from osiris_api.paper_execution import (
    _canon_json,
    _connect,
    _row_hash,
    _verify_audit,
    initialize_paper_account,
    set_kill_switch,
)
from osiris_api.phase15_audit_chain import _canonical_event, build_audit_chain
from osiris_api.phase16_durable_audit import DurableAuditStore
from osiris_api.phase18_audit_bundle import export_audit_bundle, verify_audit_bundle

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "verify_audit_evidence.py"


def _keys(tmp_path, monkeypatch):
    tmp_path.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    private = tmp_path / "audit-signing-key.pem"
    public = tmp_path / "audit-verify-key.pem"
    private.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    public.write_bytes(key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
    private.chmod(0o600)
    monkeypatch.setenv("OSIRIS_AUDIT_SIGNING_KEY_FILE", str(private))
    monkeypatch.setenv("OSIRIS_AUDIT_VERIFYING_KEY_FILE", str(public))
    monkeypatch.setenv("OSIRIS_AUDIT_ACTOR", "osiris-quant-api")
    monkeypatch.delenv("OSIRIS_AUDIT_ANCHOR_PATH", raising=False)
    monkeypatch.delenv("OSIRIS_AUDIT_CHECKPOINT_EVERY", raising=False)
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    return private, public


def _event(event_id="evt-1", **extra):
    body = {
        "event_id": event_id,
        "timestamp": "2026-11-03T02:00:00Z",
        "actor": "intruder",
        "action": "risk_gate_evaluated",
        "details": {"new_risk_allowed": False},
        "sequence": 99,
        "hash": "f" * 64,
    }
    body.update(extra)
    return body


def _paper(tmp_path, monkeypatch):
    database = tmp_path / "paper.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(database))
    initialize_paper_account("1000")
    return database


def _anchor(database, tmp_path, name="anchor.json"):
    path = tmp_path / name
    checkpoint_evidence("paper" if "paper" in database.name else "phase16", str(database), str(path))
    return path


@pytest.fixture
def signed(tmp_path, monkeypatch):
    private, public = _keys(tmp_path, monkeypatch)
    return {"private": private, "public": public, "root": tmp_path}


def test_phase16_stamps_server_actor_and_ignores_caller_links(signed, tmp_path):
    store = DurableAuditStore(str(tmp_path / "audit.sqlite3"))
    appended = store.append(_event())
    assert appended["actor"] == "osiris-quant-api"
    assert appended["actor_source"] == "server"
    assert appended["actor_authentication_verified"] is True
    assert appended["live_trading_authorized"] is False
    with sqlite3.connect(store.database_path) as db:
        stored = json.loads(db.execute("SELECT event_json FROM audit_events").fetchone()[0])
    assert stored["actor"] == "osiris-quant-api"
    assert stored["actor_source"] == "server"
    assert stored["claimed_actor"] == "intruder"
    assert "hash" not in stored
    assert "sequence" not in stored
    assert store.verify()["status"] == "valid"


def test_signed_paper_checkpoint_verifies_with_public_key_only(signed, tmp_path, monkeypatch):
    database = _paper(tmp_path, monkeypatch)
    anchor = _anchor(database, tmp_path)
    monkeypatch.delenv("OSIRIS_AUDIT_SIGNING_KEY_FILE")
    result = verify_evidence(
        "paper", database=str(database), anchor=str(anchor), public_key_path=str(signed["public"]),
    )
    assert result["status"] == "ok"
    assert result["signed"] is True
    assert result["signature_status"] == "verified"
    assert result["actor_authentication_verified"] is True
    assert result["trusted_timestamp_verified"] is False
    assert result["live_execution_enabled"] is False
    assert result["live_trading_authorized"] is False
    assert result["new_risk_allowed"] is False
    assert b"PRIVATE KEY" not in anchor.read_bytes()


def _book(tmp_path, monkeypatch, name):
    root = tmp_path / name
    root.mkdir()
    database = root / "paper.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(database))
    initialize_paper_account("1000")
    set_kill_switch(True)
    set_kill_switch(False)
    anchor = root / "anchor.json"
    checkpoint_evidence("paper", str(database), str(anchor))
    return database, anchor


def test_edit_delete_reorder_and_truncate_are_tampered(signed, tmp_path, monkeypatch):
    edited_db, edited_anchor = _book(tmp_path, monkeypatch, "edit")
    with sqlite3.connect(edited_db) as db:
        db.execute("UPDATE paper_exec_audit SET payload='{}' WHERE seq=2")
    edited = verify_evidence(
        "paper", database=str(edited_db), anchor=str(edited_anchor),
        public_key_path=str(signed["public"]),
    )
    assert edited["status"] == "tampered"
    assert edited["reason"] == "hash_chain_inconsistent"

    deleted_db, deleted_anchor = _book(tmp_path, monkeypatch, "delete")
    with sqlite3.connect(deleted_db) as db:
        db.execute("DELETE FROM paper_exec_audit WHERE seq=2")
    deleted = verify_evidence(
        "paper", database=str(deleted_db), anchor=str(deleted_anchor),
        public_key_path=str(signed["public"]),
    )
    assert deleted["status"] == "tampered"
    assert deleted["reason"] == "hash_chain_inconsistent"

    reordered_db, reordered_anchor = _book(tmp_path, monkeypatch, "reorder")
    with sqlite3.connect(reordered_db) as db:
        db.execute("UPDATE paper_exec_audit SET seq = -seq")
        db.execute(
            "UPDATE paper_exec_audit SET seq = CASE seq WHEN -1 THEN 3 WHEN -3 THEN 1 ELSE -seq END"
        )
    reordered = verify_evidence(
        "paper", database=str(reordered_db), anchor=str(reordered_anchor),
        public_key_path=str(signed["public"]),
    )
    assert reordered["status"] == "tampered"
    assert reordered["reason"] == "hash_chain_inconsistent"

    truncated_db, truncated_anchor = _book(tmp_path, monkeypatch, "truncate")
    with sqlite3.connect(truncated_db) as db:
        db.execute("DELETE FROM paper_exec_audit WHERE seq = (SELECT MAX(seq) FROM paper_exec_audit)")
    truncated = verify_evidence(
        "paper", database=str(truncated_db), anchor=str(truncated_anchor),
        public_key_path=str(signed["public"]),
    )
    assert truncated["status"] == "tampered"
    assert truncated["reason"] == "truncated"
    assert truncated["status"] != "ok"


def test_genesis_rewrite_passes_the_internal_hash_and_fails_the_anchor(signed, tmp_path, monkeypatch):
    database = _paper(tmp_path, monkeypatch)
    anchor = _anchor(database, tmp_path)
    payload = _canon_json({"replaced": True, "live_execution": False})
    digest = _row_hash("GENESIS", 1, "account_initialized", None, payload)
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM paper_exec_audit")
        conn.execute(
            "INSERT INTO paper_exec_audit(seq, client_order_id, event, payload, prev_hash, row_hash) "
            "VALUES (1, NULL, ?, ?, ?, ?)",
            ("account_initialized", payload, "GENESIS", digest),
        )
        _verify_audit(conn)
        conn.commit()
    finally:
        conn.close()
    result = verify_evidence(
        "paper", database=str(database), anchor=str(anchor), public_key_path=str(signed["public"]),
    )
    assert result["status"] == "tampered"
    assert result["reason"] == "anchor_mismatch"
    assert result["signed"] is True


def test_bad_signature_is_tampered(signed, tmp_path, monkeypatch):
    database = _paper(tmp_path, monkeypatch)
    anchor = _anchor(database, tmp_path)
    document = json.loads(anchor.read_text())
    document["evidence"]["signature"] = base64.b64encode(b"\x01" * 64).decode()
    anchor.write_text(json.dumps(document))
    result = verify_evidence(
        "paper", database=str(database), anchor=str(anchor), public_key_path=str(signed["public"]),
    )
    assert result["status"] == "tampered"
    assert result["reason"] == "bad_signature"
    assert result["signed"] is False


def test_missing_key_is_unsigned_and_unverifiable(tmp_path, monkeypatch):
    monkeypatch.delenv("OSIRIS_AUDIT_SIGNING_KEY_FILE", raising=False)
    monkeypatch.delenv("OSIRIS_AUDIT_VERIFYING_KEY_FILE", raising=False)
    monkeypatch.setenv("OSIRIS_AUDIT_ACTOR", "osiris-quant-api")
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    database = _paper(tmp_path, monkeypatch)
    anchor = tmp_path / "unsigned.json"
    envelope = checkpoint_evidence("paper", str(database), str(anchor))
    assert envelope["signed"] is False
    assert envelope["signature_status"] == "unsigned"
    assert envelope["evidence"]["signature"] is None
    result = verify_evidence("paper", database=str(database), anchor=str(anchor))
    assert result["status"] == "unverifiable"
    assert result["signature_status"] == "unsigned"
    assert result["signed"] is False
    assert result["trusted_timestamp_verified"] is False

    private, public = _keys(tmp_path / "later", monkeypatch)
    signed_anchor = _anchor(database, tmp_path, "signed.json")
    monkeypatch.delenv("OSIRIS_AUDIT_SIGNING_KEY_FILE")
    monkeypatch.delenv("OSIRIS_AUDIT_VERIFYING_KEY_FILE")
    missing = verify_evidence("paper", database=str(database), anchor=str(signed_anchor))
    assert missing["status"] == "unverifiable"
    assert missing["signature_status"] == "unverified"
    assert missing["signed"] is False
    assert private.is_file() and public.is_file()


def test_phase15_caller_chain_is_never_ok():
    chain = build_audit_chain([{
        "event_id": "evt-1", "timestamp": "2026-11-02T20:00:00Z",
        "actor": "operator", "action": "risk_review",
    }])
    assert chain["durable_storage"] is False
    assert chain["actor_authentication_verified"] is False
    result = verify_evidence("phase15", caller_chain=chain)
    assert result["status"] == "unverifiable"
    assert result["reason"] == "caller_supplied_chain"
    assert result["actor_authentication_verified"] is False
    chain["events"][0]["event"]["action"] = "approve"
    tampered = verify_evidence("phase15", caller_chain=chain)
    assert tampered["status"] == "tampered"
    assert verify_evidence("phase15", caller_chain=None)["status"] == "unverifiable"


def test_phase16_consistent_rewrite_fails_only_against_the_anchor(signed, tmp_path, monkeypatch):
    path = tmp_path / "audit.sqlite3"
    store = DurableAuditStore(str(path))
    store.append(_event())
    store.append(_event("evt-2"))
    anchor = tmp_path / "phase16.json"
    checkpoint_evidence("phase16", str(path), str(anchor))
    event = {
        "event_id": "evt-1", "timestamp": "2026-11-03T02:00:00Z", "actor": "osiris-quant-api",
        "actor_source": "server", "actor_authentication_verified": True, "action": "rewritten",
    }
    encoded = _canonical_event(event).decode()
    previous = "0" * 64
    digest = hashlib.sha256(_canonical_event({
        "sequence": 0, "previous_hash": previous, "event": event,
    })).hexdigest()
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM audit_events")
        db.execute(
            "INSERT INTO audit_events(sequence,event_id,previous_hash,event_json,event_hash) "
            "VALUES (0, ?, ?, ?, ?)",
            ("evt-1", previous, encoded, digest),
        )
    monkeypatch.setenv("OSIRIS_AUDIT_ANCHOR_PATH", str(anchor))
    checked = store.verify()
    assert checked["status"] == "valid"
    assert checked["worm_or_privileged_rewrite_protection"] is False
    assert checked["evidence_status"] == "tampered"
    assert checked["signed"] is True
    evidence = verify_evidence(
        "phase16", database=str(path), anchor=str(anchor), public_key_path=str(signed["public"]),
    )
    assert evidence["status"] == "tampered"
    assert evidence["reason"] == "truncated"


def test_phase18_bundle_signature_fails_closed(signed, tmp_path, monkeypatch):
    path = tmp_path / "audit.sqlite3"
    store = DurableAuditStore(str(path))
    store.append(_event())
    signed_bundle = export_audit_bundle(store, "evidence-001")
    checked = verify_audit_bundle(signed_bundle)
    assert checked["signature_verified"] is True
    assert checked["evidence_status"] == "ok"
    assert checked["signed"] is True
    evidence = verify_evidence("phase18", bundle=signed_bundle, public_key_path=str(signed["public"]))
    assert evidence["status"] == "ok"
    monkeypatch.delenv("OSIRIS_AUDIT_SIGNING_KEY_FILE")
    monkeypatch.delenv("OSIRIS_AUDIT_VERIFYING_KEY_FILE")
    unsigned = export_audit_bundle(store, "evidence-002")
    assert unsigned["signed"] is False
    assert unsigned["signature_status"] == "unsigned"
    assert "signature_envelope" not in unsigned
    unsigned_check = verify_audit_bundle(unsigned)
    assert unsigned_check["status"] == "valid"
    assert unsigned_check["signature_verified"] is False
    assert unsigned_check["evidence_status"] == "unverifiable"
    assert verify_evidence("phase18", bundle=unsigned)["status"] == "unverifiable"
    without_key = verify_audit_bundle(signed_bundle)
    assert without_key["status"] == "valid"
    assert without_key["evidence_status"] == "unverifiable"
    assert without_key["signature_verified"] is False
    assert without_key["signed"] is False
    assert verify_evidence("phase18", bundle=signed_bundle)["status"] == "unverifiable"
    signed_bundle["signature_envelope"]["signature"] = base64.b64encode(b"\x02" * 64).decode()
    monkeypatch.setenv("OSIRIS_AUDIT_VERIFYING_KEY_FILE", str(signed["public"]))
    broken = verify_audit_bundle(signed_bundle)
    assert broken["status"] == "invalid"
    assert broken["signature_verified"] is False
    assert verify_evidence(
        "phase18", bundle=signed_bundle, public_key_path=str(signed["public"]),
    )["status"] == "tampered"


def test_checkpoint_survives_a_later_append_and_missing_evidence_is_unverifiable(signed, tmp_path, monkeypatch):
    database = _paper(tmp_path, monkeypatch)
    anchor = _anchor(database, tmp_path)
    set_kill_switch(True)
    result = verify_evidence(
        "paper", database=str(database), anchor=str(anchor), public_key_path=str(signed["public"]),
    )
    assert result["status"] == "ok"
    assert result["event_count"] == 2
    missing_anchor = verify_evidence("paper", database=str(database))
    assert missing_anchor["status"] == "unverifiable"
    assert missing_anchor["reason"] == "anchor_missing"
    missing_chain = verify_evidence(
        "paper", database=str(tmp_path / "absent.sqlite3"), anchor=str(anchor),
        public_key_path=str(signed["public"]),
    )
    assert missing_chain["status"] == "unverifiable"
    assert missing_chain["status"] != "ok"


def test_rfc3161_stub_never_counts_as_a_trusted_timestamp(signed, tmp_path, monkeypatch):
    database = _paper(tmp_path, monkeypatch)
    anchor = _anchor(database, tmp_path)
    document = json.loads(anchor.read_text())
    stub = document["evidence"]["payload"]["rfc3161"]
    assert stub["status"] == "stub"
    assert stub["tsa_contacted"] is False
    assert stub["token"] is None
    verified = verify_evidence(
        "paper", database=str(database), anchor=str(anchor), public_key_path=str(signed["public"]),
    )
    assert verified["trusted_timestamp_verified"] is False
    stub["tsa_contacted"] = True
    from osiris_api.control_plane.database import digest
    document["evidence"]["sha256"] = digest(document["evidence"]["payload"])
    document["evidence"]["signature"] = None
    document["signature_status"] = "unsigned"
    document["signed"] = False
    anchor.write_text(json.dumps(document))
    rejected = verify_evidence("paper", database=str(database), anchor=str(anchor))
    assert rejected["status"] == "unverifiable"
    assert rejected["reason"] == "rfc3161_stub_only"
    assert rejected["trusted_timestamp_verified"] is False


def test_anchor_is_outside_the_database_and_refreshes_phase16(signed, tmp_path, monkeypatch):
    path = tmp_path / "audit.sqlite3"
    store = DurableAuditStore(str(path))
    store.append(_event())
    with pytest.raises(ValueError, match="outside"):
        checkpoint_evidence("phase16", str(path), str(path))
    anchor = tmp_path / "outside.json"
    monkeypatch.setenv("OSIRIS_AUDIT_ANCHOR_PATH", str(anchor))
    assert store.append(_event("evt-2"))["checkpoint"] == "written"
    assert anchor.is_file()
    assert anchor.resolve() != path.resolve()
    assert verify_evidence(
        "phase16", database=str(path), anchor=str(anchor), public_key_path=str(signed["public"]),
    )["status"] == "ok"


def test_cli_verifies_offline_and_reports_tamper(signed, tmp_path, monkeypatch):
    database = _paper(tmp_path, monkeypatch)
    anchor = tmp_path / "cli.json"
    env = os.environ.copy()
    env["OSIRIS_AUDIT_SIGNING_KEY_FILE"] = str(signed["private"])
    env["OSIRIS_AUDIT_ACTOR"] = "osiris-quant-api"
    wrote = subprocess.run(
        [sys.executable, str(SCRIPT), "checkpoint", "--chain", "paper",
         "--database", str(database), "--anchor", str(anchor)],
        check=False, capture_output=True, text=True, env=env,
    )
    assert wrote.returncode == 0
    assert json.loads(wrote.stdout)["signed"] is True
    verify_env = os.environ.copy()
    verify_env.pop("OSIRIS_AUDIT_SIGNING_KEY_FILE", None)
    verify_env.pop("OSIRIS_AUDIT_VERIFYING_KEY_FILE", None)
    ok = subprocess.run(
        [sys.executable, str(SCRIPT), "verify", "--chain", "paper",
         "--database", str(database), "--anchor", str(anchor),
         "--public-key", str(signed["public"])],
        check=False, capture_output=True, text=True, env=verify_env,
    )
    assert ok.returncode == 0
    assert json.loads(ok.stdout)["status"] == "ok"
    with sqlite3.connect(database) as db:
        db.execute("UPDATE paper_exec_audit SET payload='{}'")
    tampered = subprocess.run(
        [sys.executable, str(SCRIPT), "verify", "--chain", "paper",
         "--database", str(database), "--anchor", str(anchor),
         "--public-key", str(signed["public"])],
        check=False, capture_output=True, text=True, env=verify_env,
    )
    assert tampered.returncode == 1
    assert json.loads(tampered.stdout)["status"] == "tampered"
