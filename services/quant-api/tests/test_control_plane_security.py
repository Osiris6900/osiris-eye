import base64
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from osiris_api.control_plane.api import router
from osiris_api.control_plane.database import Database
from osiris_api.control_plane.identity import IdentityService, totp
from osiris_api.control_plane.secrets import EncryptedSecrets, VaultSecrets


def test_rfc6238_vector_and_sessions(tmp_path):
    seed = base64.b32encode(b"12345678901234567890").decode()
    assert totp(seed, 1, digits=8) == "94287082"
    now = [1_800_000_000.0]
    db = Database(str(tmp_path / "identity.db"))
    identity = IdentityService(EncryptedSecrets(db, b"a" * 32), clock=lambda: now[0])
    secret = identity.enroll("alice")
    token = identity.login("alice", "analyst", totp(secret, int(now[0] // 30)), "config-a")
    assert identity.session(token, "config-a")["actor"] == "alice"
    with pytest.raises(ValueError, match="replayed"):
        identity.login("alice", "analyst", totp(secret, int(now[0] // 30)), "config-a")
    with pytest.raises(ValueError, match="already_enrolled"):
        identity.enroll("alice")
    with pytest.raises(ValueError, match="invalid_session"):
        identity.session(token, "rotated-config")
    identity.logout(token, "config-a")
    with pytest.raises(ValueError, match="invalid_session"):
        identity.session(token, "config-a")
    now[0] += 30
    second = identity.login("alice", "analyst", totp(secret, int(now[0] // 30)), "config-a")
    now[0] += 901
    with pytest.raises(ValueError, match="invalid_session"):
        identity.session(second, "config-a")


def test_mfa_lockout_persists_across_restarts(tmp_path):
    now = [1_800_000_000.0]
    db = Database(str(tmp_path / "identity.db"))
    identity = IdentityService(EncryptedSecrets(db, b"a" * 32), clock=lambda: now[0])
    seed = identity.enroll("alice")
    for _ in range(5):
        with pytest.raises(ValueError):
            identity.verify("alice", "bad")
    restarted = IdentityService(EncryptedSecrets(db, b"a" * 32), clock=lambda: now[0])
    with pytest.raises(ValueError, match="unavailable"):
        restarted.verify("alice", totp(seed, int(now[0] // 30)))
    now[0] += 301
    restarted.verify("alice", totp(seed, int(now[0] // 30)))


def test_expired_session_cannot_revive_after_clock_rollback_and_restart(tmp_path):
    now = [1_800_000_000.0]
    db = Database(str(tmp_path / "identity.db"))
    identity = IdentityService(EncryptedSecrets(db, b"a" * 32), clock=lambda: now[0])
    seed = identity.enroll("alice")
    token = identity.login("alice", "analyst", totp(seed, int(now[0] // 30)), "config")
    now[0] += 901
    with pytest.raises(ValueError, match="invalid_session"):
        identity.session(token, "config")
    now[0] -= 900
    restarted = IdentityService(EncryptedSecrets(Database(db.path), b"a" * 32),
                                clock=lambda: now[0])
    with pytest.raises(ValueError, match="clock_rollback"):
        restarted.session(token, "config")


def test_expired_enrollment_cannot_revive_after_refusal_and_clock_rollback(tmp_path):
    now = [1_800_000_000.0]
    db = Database(str(tmp_path / "identity.db"))
    identity = IdentityService(EncryptedSecrets(db, b"a" * 32), clock=lambda: now[0])
    seed = identity.enroll("alice")
    now[0] += 601
    with pytest.raises(ValueError, match="mfa_unavailable"):
        identity.verify("alice", totp(seed, int(now[0] // 30)))
    now[0] -= 600
    restarted = IdentityService(EncryptedSecrets(Database(db.path), b"a" * 32),
                                clock=lambda: now[0])
    with pytest.raises(ValueError, match="identity_clock_rollback"):
        restarted.login("alice", "analyst", totp(seed, int(now[0] // 30)), "config")
    assert db.list("sessions") == []
    assert db.get("mfa", "alice")["value"]["confirmed"] is False


def test_refused_reenrollment_fences_expired_sessions_without_changing_seed(tmp_path):
    now = [1_800_000_000.0]
    db = Database(str(tmp_path / "identity.db"))
    store = EncryptedSecrets(db, b"a" * 32)
    identity = IdentityService(store, clock=lambda: now[0])
    seed = identity.enroll("alice")
    token = identity.login("alice", "analyst", totp(seed, int(now[0] // 30)), "config")
    original = db.get("mfa", "alice")
    now[0] += 901
    with pytest.raises(ValueError, match="mfa_already_enrolled"):
        identity.enroll("alice")
    assert db.get("mfa", "alice") == original
    assert store.get("totp:alice") == seed
    now[0] -= 900
    restarted = IdentityService(EncryptedSecrets(Database(db.path), b"a" * 32),
                                clock=lambda: now[0])
    with pytest.raises(ValueError, match="identity_clock_rollback"):
        restarted.session(token, "config")


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1, "bad", True, 1e40])
def test_invalid_identity_clock_never_accepts_session(tmp_path, invalid):
    now = [1_800_000_000.0]
    identity = IdentityService(EncryptedSecrets(Database(str(tmp_path / "identity.db")),
                                                b"a" * 32), clock=lambda: now[0])
    seed = identity.enroll("alice")
    token = identity.login("alice", "analyst", totp(seed, int(now[0] // 30)), "config")
    now[0] = invalid
    with pytest.raises(ValueError, match="invalid_identity_clock"):
        identity.session(token, "config")


def test_secrets_are_encrypted_and_bound_to_name(tmp_path):
    db = Database(str(tmp_path / "secrets.db"))
    vault = EncryptedSecrets(db, b"a" * 32)
    vault.put("provider-a", "TEST-SECRET-DO-NOT-USE")
    assert b"TEST-SECRET-DO-NOT-USE" not in (tmp_path / "secrets.db").read_bytes()
    assert vault.get("provider-a") == "TEST-SECRET-DO-NOT-USE"
    record = db.get("secrets", "provider-a")["value"]
    db.put("secrets", "provider-b", record)
    with pytest.raises(ValueError, match="decryption"):
        vault.get("provider-b")
    with pytest.raises(ValueError, match="decryption"):
        EncryptedSecrets(db, b"b" * 32).get("provider-a")


def test_vault_never_echoes_upstream_secrets():
    class Broken:
        def request(self, *args, **kwargs):
            raise ValueError("LEAKED-SECRET")

    vault = VaultSecrets("https://vault.example.invalid", "TEST-TOKEN", Broken())
    with pytest.raises(ValueError, match="^vault_read_failed$"):
        vault.get("provider-a")
    with pytest.raises(ValueError):
        VaultSecrets("http://vault.example.invalid", "TEST-TOKEN", Broken())


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_CONTROL_DB", str(tmp_path / "control.db"))
    monkeypatch.setenv("OSIRIS_CONTROL_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv(
        "OSIRIS_ADMIN_TOKENS",
        json.dumps(
            {
                "analyst": "test-analyst-token",
                "operator": "test-operator-token",
                "viewer": "test-viewer-token",
            }
        ),
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def session(client, role):
    header = {"Authorization": "Bearer test-" + role + "-token"}
    enrollment = client.post("/control-plane/auth/enroll", headers=header)
    assert enrollment.status_code == 200, enrollment.text
    code = totp(enrollment.json()["secret"], int(time.time() // 30))
    login = client.post("/control-plane/auth/login", headers=header, json={"code": code})
    assert login.status_code == 200, login.text
    return {"Authorization": "Session " + login.json()["session_token"]}


def test_api_defaults_closed_and_viewer_cannot_read_research(api, monkeypatch):
    assert api.get("/control-plane/status").status_code == 401
    viewer = {"Authorization": "Bearer test-viewer-token"}
    assert api.get("/control-plane/status", headers=viewer).status_code == 200
    assert api.get("/control-plane/reports", headers=viewer).status_code == 403
    assert api.get("/control-plane/jobs", headers=viewer).status_code == 403
    monkeypatch.delenv("OSIRIS_CONTROL_DB")
    assert api.get("/control-plane/status", headers=viewer).status_code == 503


def test_reviewed_job_flow_and_fenced_api_authorization(api):
    payload = {
        "id": "experiment-1",
        "tool": "backtest",
        "payload": {"signals": [0, 1, 0], "asset_returns": [0, 0.1, -0.1]},
    }
    assert (
        api.post(
            "/control-plane/agents/requests",
            headers={"Authorization": "Bearer test-analyst-token"},
            json=payload,
        ).status_code
        == 403
    )
    analyst, operator = session(api, "analyst"), session(api, "operator")
    assert (
        api.post("/control-plane/agents/requests", headers=analyst, json=payload).json()["status"]
        == "awaiting_review"
    )
    assert (
        api.post("/control-plane/agents/experiment-1/approve", headers=analyst).status_code == 403
    )
    assert (
        api.post("/control-plane/agents/experiment-1/approve", headers=operator).status_code == 200
    )
    assert (
        api.post(
            "/control-plane/workers/claim", headers=analyst, json={"kinds": ["backtest"]}
        ).status_code
        == 403
    )
    claim = api.post(
        "/control-plane/workers/claim", headers=operator, json={"kinds": ["backtest"]}
    ).json()["job"]
    assert claim["id"] == "experiment-1"
    assert (
        api.post(
            "/control-plane/workers/experiment-1/finish",
            headers=operator,
            json={"claim_token": "forged", "result": {}},
        ).status_code
        == 422
    )
    assert (
        api.post(
            "/control-plane/workers/experiment-1/finish",
            headers=operator,
            json={"claim_token": claim["claim_token"], "result": {"periods": 2}},
        ).status_code
        == 200
    )
    assert (
        api.get("/control-plane/reports", headers=analyst).json()["jobs"][0]["status"]
        == "completed"
    )
    assert api.post("/control-plane/auth/logout", headers=operator).status_code == 200
    assert api.get("/control-plane/jobs", headers=operator).status_code == 401


def test_primary_token_rotation_preserves_mfa_identity_and_self_review_block(api, monkeypatch):
    old = {"Authorization": "Bearer test-operator-token"}
    seed = api.post("/control-plane/auth/enroll", headers=old).json()["secret"]
    counter = int(time.time() // 30)
    initial = api.post(
        "/control-plane/auth/login", headers=old, json={"code": totp(seed, counter)}
    ).json()["session_token"]
    header = {"Authorization": "Session " + initial}
    request = {
        "id": "rotation-test",
        "tool": "backtest",
        "payload": {"signals": [0, 1], "asset_returns": [0, 0.1]},
    }
    assert (
        api.post("/control-plane/agents/requests", headers=header, json=request).status_code == 200
    )
    import os

    tokens = json.loads(os.environ["OSIRIS_ADMIN_TOKENS"])
    tokens["operator"] = "rotated-operator-test-token"
    monkeypatch.setenv("OSIRIS_ADMIN_TOKENS", json.dumps(tokens))
    assert api.get("/control-plane/jobs", headers=header).status_code == 401
    replacement = {"Authorization": "Bearer rotated-operator-test-token"}
    assert api.post("/control-plane/auth/enroll", headers=replacement).status_code == 422
    next_session = api.post(
        "/control-plane/auth/login", headers=replacement, json={"code": totp(seed, counter + 1)}
    ).json()["session_token"]
    denied = api.post(
        "/control-plane/agents/rotation-test/approve",
        headers={"Authorization": "Session " + next_session},
    )
    assert denied.status_code == 422
    assert "independent_approver_required" in denied.text
