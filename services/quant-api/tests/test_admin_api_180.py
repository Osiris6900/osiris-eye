"""Issue #180: admin API contract, RBAC enforcement and secrets hygiene.

Every test runs through the real FastAPI app with TestClient; the admin
router's fail-closed RBAC is the single gate for /admin/ routes.
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from osiris_api import admin_centre, authorized_feeds
from osiris_api.authorized_feeds import FeedAdapterRejection, SyntheticAuthorizedAdapter
from osiris_api.main import app

TOKENS = {
    "viewer": "vtk-viewer-9f2e71aa",
    "analyst": "vtk-analyst-44bd90c2",
    "operator": "vtk-operator-7c1d55e0",
    "risk_officer": "vtk-riskoff-83aa10f6",
    "security_admin": "vtk-secadmin-5b77c31d",
}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv(admin_centre.TOKENS_ENV, json.dumps(TOKENS))
    monkeypatch.delenv("OSIRIS_API_TOKEN", raising=False)
    monkeypatch.delenv("OSIRIS_ENV", raising=False)
    monkeypatch.setenv(admin_centre.ENABLE_ENV, "0")  # setenv (not delenv) so monkeypatch undo also removes endpoint writes
    monkeypatch.setenv("OSIRIS_PAPER_LEDGER_DB", str(tmp_path / "paper.sqlite3"))
    monkeypatch.delenv(admin_centre.OUTBOX_PATH_ENV, raising=False)
    admin_centre.reset_admin_state()
    yield TestClient(app)
    admin_centre.reset_admin_state()


def _headers(role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[role]}"}


# ---------------------------------------------------------------------------
# Fail-closed configuration and authentication


def test_unconfigured_admin_tokens_return_503(client, monkeypatch):
    monkeypatch.delenv(admin_centre.TOKENS_ENV, raising=False)
    response = client.get("/admin/overview")
    assert response.status_code == 503
    assert response.json()["detail"] == "admin_not_configured"


def test_missing_and_wrong_tokens_return_401(client):
    assert client.get("/admin/overview").status_code == 401
    bad = client.get("/admin/overview", headers={"Authorization": "Bearer wrong-token-000"})
    assert bad.status_code == 401
    assert bad.json()["detail"] == "admin_authentication_required"
    no_scheme = client.get("/admin/overview", headers={"Authorization": TOKENS["viewer"]})
    assert no_scheme.status_code == 401


# ---------------------------------------------------------------------------
# Role x endpoint matrix (matrix gate: unauthorized user must never view
# privileged state or change controls)


READ_HEALTH = ["/admin/overview", "/admin/network/status", "/admin/feeds/status",
               "/admin/alerts", "/admin/audit"]
READ_RESEARCH = ["/admin/paper/status", "/admin/risk/status"]
CONTROLS = [
    ("post", "/admin/alerts/ingestion_disabled:global/ack", {}),
    ("post", "/admin/network/kill-switch", {"enabled": True}),
    ("post", "/admin/feeds/synthetic-authorized/revoke", None),
]
ALLOWED_CONTROLS = {
    "viewer": set(), "analyst": set(), "operator": {"ack"},
    "risk_officer": {"ack", "kill"}, "security_admin": {"ack", "revoke"},
}
CONTROL_KEYS = ["ack", "kill", "revoke"]


@pytest.mark.parametrize("role", sorted(TOKENS))
def test_read_endpoint_matrix(client, role):
    for path in READ_HEALTH:
        assert client.get(path, headers=_headers(role)).status_code == 200, (role, path)
    expected = 200 if role in {"analyst", "operator", "risk_officer", "security_admin"} else 403
    for path in READ_RESEARCH:
        response = client.get(path, headers=_headers(role))
        assert response.status_code == expected, (role, path)
        if expected == 403:
            assert response.json()["detail"] == "admin_permission_denied"


@pytest.mark.parametrize("role", sorted(TOKENS))
def test_control_matrix(client, role):
    # Populate the alert registry (read_health is allowed for every role).
    assert client.get("/admin/alerts", headers=_headers(role)).status_code == 200
    for (method, path, body), key in zip(CONTROLS, CONTROL_KEYS):
        request = getattr(client, method)
        response = request(path, headers=_headers(role), json=body) if body is not None \
            else request(path, headers=_headers(role))
        if key in ALLOWED_CONTROLS[role]:
            assert response.status_code == 200, (role, key, response.text)
        else:
            assert response.status_code == 403, (role, key)
        admin_centre.reset_admin_state()  # isolate control side effects per case


# ---------------------------------------------------------------------------
# Operational controls have real, auditable effects


def test_kill_switch_flips_real_ingestion_gate(client):
    before = client.get("/admin/network/status", headers=_headers("viewer")).json()
    assert before["ingestion_enabled"] is False
    response = client.post(
        "/admin/network/kill-switch", headers=_headers("risk_officer"),
        json={"enabled": True},
    )
    assert response.status_code == 200
    assert response.json()["ingestion_enabled"] is True
    assert os.environ[admin_centre.ENABLE_ENV] == "1"
    after = client.get("/admin/network/status", headers=_headers("viewer")).json()
    assert after["ingestion_enabled"] is True
    assert response.json()["live_trading_authorized"] is False


def test_kill_switch_rejects_non_boolean_state(client):
    response = client.post(
        "/admin/network/kill-switch", headers=_headers("risk_officer"),
        json={"enabled": "yes"},
    )
    assert response.status_code == 422


def test_revoke_suspends_adapter_and_blocks_ingestion(client):
    response = client.post(
        "/admin/feeds/synthetic-authorized/revoke", headers=_headers("security_admin")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["suspended"] is True and body["durable"] is False
    feeds = client.get("/admin/feeds/status", headers=_headers("viewer")).json()
    assert feeds["suspended_count"] == 1
    # Cross-layer proof: the ingestion gate itself now refuses the adapter.
    from datetime import datetime, timedelta, timezone

    adapter = SyntheticAuthorizedAdapter(
        seed="admin-api-180", symbols=["SYNTH-PERP"],
        start=datetime.now(timezone.utc) - timedelta(minutes=15),
    )
    with pytest.raises(FeedAdapterRejection, match="adapter_suspended"):
        authorized_feeds.ingest_adapter_ticks(
            adapter, instruments=["SYNTH-PERP"], now=datetime.now(timezone.utc)
        )


def test_revoke_unknown_adapter_404(client):
    response = client.post("/admin/feeds/nope/revoke", headers=_headers("security_admin"))
    assert response.status_code == 404


def test_alert_ack_flow(client):
    listing = client.get("/admin/alerts", headers=_headers("operator")).json()
    kinds = {a["alert_id"] for a in listing["active"]}
    assert "ingestion_disabled:global" in kinds
    acked = client.post(
        "/admin/alerts/ingestion_disabled:global/ack", headers=_headers("operator")
    )
    assert acked.status_code == 200
    assert acked.json()["alert"]["acknowledged_by"] == "operator"
    assert client.post(
        "/admin/alerts/ingestion_disabled:global/ack", headers=_headers("operator")
    ).status_code == 404


# ---------------------------------------------------------------------------
# Audit and secrets hygiene (matrix gate: secrets never appear in responses)


def test_audit_records_allow_and_deny_decisions(client):
    client.get("/admin/paper/status", headers=_headers("viewer"))  # 403
    client.get("/admin/overview")  # 401
    client.get("/admin/overview", headers=_headers("viewer"))  # 200
    entries = client.get("/admin/audit", headers=_headers("viewer")).json()["entries"]
    triples = {(e["role"], e["action"], e["decision"]) for e in entries}
    assert ("viewer", "read_research", "deny") in triples
    assert ("unknown", "read_health", "deny") in triples
    assert ("viewer", "read_health", "allow") in triples
    assert all(e["schema"] == admin_centre.AUDIT_SCHEMA for e in entries)


def test_no_configured_secret_appears_in_any_admin_response(client):
    for role, token in TOKENS.items():
        can_research = role != "viewer"
        for path in READ_HEALTH + READ_RESEARCH:
            response = client.get(path, headers=_headers(role))
            if path in READ_RESEARCH and not can_research:
                assert response.status_code == 403  # denial bodies are scanned too
            else:
                assert response.status_code == 200
            for secret in TOKENS.values():
                assert secret not in response.text, (role, path)
        client.post("/admin/network/kill-switch", headers=_headers(role), json={"enabled": True})
        admin_centre.reset_admin_state()


def test_response_redaction_fails_closed(client, monkeypatch):
    real = admin_centre.paper_status

    def poisoned(*, now=None):
        return {
            **real(now=now),
            "positions": [{"symbol": "SYNTH", "api_token": TOKENS["analyst"]}],
        }

    monkeypatch.setattr(admin_centre, "paper_status", poisoned)
    response = client.get("/admin/paper/status", headers=_headers("analyst"))
    assert response.status_code == 500
    assert response.json()["detail"] == "admin_response_redacted"
    assert TOKENS["analyst"] not in response.text
    entries = client.get("/admin/audit", headers=_headers("analyst")).json()["entries"]
    assert any(e["decision"] == "deny" and "admin_response_redacted" in e["detail"]
               for e in entries)


# ---------------------------------------------------------------------------
# API contract


def test_contract_schemas_and_keys(client):
    overview = client.get("/admin/overview", headers=_headers("viewer")).json()
    assert overview["schema"] == "osiris.admin_overview.v1"
    assert set(overview) == {
        "schema", "generated_at", "service", "api_version", "role", "roles_configured",
        "ingestion_enabled", "feeds", "paper_initialized", "active_alert_count",
        "risk_flag_count", "live_trading_authorized",
    }
    assert overview["role"] == "viewer"
    assert overview["live_trading_authorized"] is False

    network = client.get("/admin/network/status", headers=_headers("viewer")).json()
    assert network["schema"] == "osiris.admin_network_status.v1"
    assert set(network) == {
        "schema", "generated_at", "ingestion_enabled", "kill_switch_env", "outbox",
        "peer_telemetry", "live_trading_authorized",
    }

    feeds = client.get("/admin/feeds/status", headers=_headers("viewer")).json()
    assert feeds["schema"] == "osiris.admin_feeds_status.v1"
    assert set(feeds) == {
        "schema", "generated_at", "adapter_count", "suspended_count",
        "stale_review_count", "quality_review_max_age_seconds", "adapters",
    }
    assert set(feeds["adapters"][0]) == {
        "adapter", "entitled", "suspended", "instruments", "quality_review",
    }

    paper = client.get("/admin/paper/status", headers=_headers("analyst")).json()
    assert paper["schema"] == "osiris.admin_paper_status.v1"
    assert set(paper) == {
        "schema", "generated_at", "mode", "initialized", "cash", "positions",
        "fill_count", "pnl", "marks_available", "live_trading_authorized",
    }
    assert paper["mode"] == "synthetic-paper-only"

    risk = client.get("/admin/risk/status", headers=_headers("analyst")).json()
    assert risk["schema"] == "osiris.admin_risk_status.v1"
    assert set(risk) == {
        "schema", "generated_at", "flag_count", "flags", "exposure", "risk_gate",
        "live_trading_authorized",
    }


def test_admin_exemption_does_not_weaken_outer_middleware(client, monkeypatch):
    monkeypatch.setenv("OSIRIS_API_TOKEN", "outer-token-xyz123")
    # Admin routes still answer via their own RBAC, not the outer token.
    assert client.get("/admin/overview", headers=_headers("viewer")).status_code == 200
    assert client.get("/admin/overview").status_code == 401
    # Non-admin routes are still protected by the outer middleware.
    assert client.get("/platform/modules").status_code == 401
    assert client.get(
        "/platform/modules", headers={"Authorization": "Bearer outer-token-xyz123"}
    ).status_code == 200
