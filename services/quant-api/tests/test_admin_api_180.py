"""Issue #180: admin API contract, RBAC enforcement and secrets hygiene.

Every test runs through the real FastAPI app with TestClient; the admin
router's fail-closed RBAC is the single gate for /admin/ routes.
"""
from __future__ import annotations

import json
import os
from unittest.mock import patch

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
    monkeypatch.setenv(admin_centre.IDENTITY_DB_ENV, str(tmp_path / "p2p.sqlite3"))
    monkeypatch.delenv("OSIRIS_P2P_LOCAL_HEALTH_PORT", raising=False)
    monkeypatch.setenv(admin_centre.ALERT_DB_ENV, str(tmp_path / "alerts.sqlite3"))
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


def test_contract_schemas_and_keys(client, tmp_path):
    overview = client.get("/admin/overview", headers=_headers("viewer")).json()
    assert overview["schema"] == "osiris.admin_overview.v1"
    assert set(overview) == {
        "schema", "generated_at", "service", "api_version", "role", "roles_configured",
        "ingestion_enabled", "feeds", "paper_initialized", "active_alert_count",
        "risk_flag_count", "peer_health", "enrolled_peer_count", "outbox_pending",
        "live_trading_authorized",
    }
    assert overview["peer_health"] == "unavailable"
    assert overview["enrolled_peer_count"] is None
    assert overview["outbox_pending"] is None
    assert overview["role"] == "viewer"
    assert overview["live_trading_authorized"] is False

    network = client.get("/admin/network/status", headers=_headers("viewer")).json()
    assert network["schema"] == "osiris.admin_network_status.v1"
    assert set(network) == {
        "schema", "generated_at", "ingestion_enabled", "kill_switch_env", "outbox",
        "peer_telemetry", "peer_health", "local_live_node", "live_trading_authorized",
    }
    assert network["peer_health"]["peer_count"] is None
    assert network["peer_health"]["status"] == "unavailable"
    assert network["peer_health"]["reason"] == "registry_file_absent"
    assert network["peer_health"]["nodes"] is None
    assert network["peer_health"]["liveness"] == "not_observed"
    assert not (tmp_path / "p2p.sqlite3").exists()

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
        "fill_count", "recent_fills", "pnl", "cost_basis_gross", "market_value",
        "open_orders", "open_orders_reason", "marks_available", "live_trading_authorized",
    }
    assert paper["open_orders"] is None
    assert paper["open_orders_reason"] == "not_stored"
    assert paper["cost_basis_gross"] is None
    assert paper["mode"] == "synthetic-paper-only"

    risk = client.get("/admin/risk/status", headers=_headers("analyst")).json()
    assert risk["schema"] == "osiris.admin_risk_status.v1"
    assert set(risk) == {
        "schema", "generated_at", "flag_count", "flags", "exposure", "exposure_basis",
        "cost_basis_gross", "market_value", "risk_gate", "live_trading_authorized",
    }
    assert risk["exposure"] == "unavailable_without_marks"


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


def test_alerts_report_durable_history_and_require_authentication(client):
    anonymous = client.get("/admin/alerts")
    assert anonymous.status_code == 401
    assert anonymous.json()["detail"] == "admin_authentication_required"
    allowed = client.get("/admin/alerts", headers=_headers("viewer"))
    assert allowed.status_code == 200
    body = allowed.json()
    assert body["storage"] == "sqlite"
    assert body["durable"] is True
    assert body["initialized"] is True
    assert body["active_count"] == len(body["active"])
    assert body["acknowledged_count"] == len(body["acknowledged"])
    assert isinstance(body["active"], list)
    assert isinstance(body["acknowledged"], list)
    denied = client.get("/admin/alerts", headers={"Authorization": "Bearer not-a-role-token"})
    assert denied.status_code == 401
    for secret in TOKENS.values():
        assert secret not in allowed.text
        assert secret not in anonymous.text
        assert secret not in denied.text


def test_paper_fills_are_readable_by_research_roles_only(client):
    from osiris_api import durable_paper_ledger

    durable_paper_ledger.initialize_account("10000")
    durable_paper_ledger.synthetic_fill(
        "fill-key-do-not-leak", "SYNTH-PERP", "BUY", "2", "100", fee="0"
    )
    allowed = client.get("/admin/paper/status", headers=_headers("analyst"))
    assert allowed.status_code == 200
    body = allowed.json()
    assert body["cash"] == "9800"
    assert body["cost_basis_gross"] == "200"
    assert body["market_value"] is None
    assert body["recent_fills"][0]["side"] == "BUY"
    assert body["recent_fills"][0]["symbol"] == "SYNTH-PERP"
    assert "fill-key-do-not-leak" not in allowed.text
    denied = client.get("/admin/paper/status", headers=_headers("viewer"))
    assert denied.status_code == 403
    assert "9800" not in denied.text
    assert "SYNTH-PERP" not in denied.text


def test_network_message_status_counts_and_hides_raw_rejection_text(client, tmp_path, monkeypatch):
    import sqlite3

    from osiris_api.observation_outbox import ObservationOutbox

    path = tmp_path / "outbox.sqlite3"
    ObservationOutbox(str(path))
    database = sqlite3.connect(path)
    database.executemany(
        "INSERT INTO pending_observations VALUES (?, ?, ?, ?)",
        [
            ("pending", "{}", 0, None),
            ("delivered", "{}", 1, None),
            ("stale", "{}", 0, "observation_stale"),
            ("secret", "{}", 0, "token=supersecretvalue"),
        ],
    )
    database.commit()
    monkeypatch.setenv(admin_centre.OUTBOX_PATH_ENV, str(path))
    response = client.get("/admin/network/status", headers=_headers("viewer"))
    assert response.status_code == 200
    body = response.json()
    assert body["outbox"]["pending"] == 1
    assert body["outbox"]["delivered"] == 1
    assert body["outbox"]["rejected"] == 2
    assert body["outbox"]["delivered_meaning"] == "local_node_process_not_remote_receipt"
    reasons = {item["reason"]: item["count"] for item in body["outbox"]["rejection_reasons"]}
    assert reasons == {"observation_stale": 1, "unclassified": 1}
    assert "supersecretvalue" not in response.text
    assert body["peer_health"]["peer_count"] is None
    assert client.post(
        "/admin/network/status", headers=_headers("viewer")
    ).status_code == 405


def test_enrolled_registry_is_read_only_and_hides_key_material(client, tmp_path, monkeypatch):
    import sqlite3

    from osiris_api.p2p_identity import (
        P2PIdentityRegistry,
        generate_node_identity,
        sign_enrollment_challenge,
    )

    path = tmp_path / "nodes.sqlite3"
    trusted = generate_node_identity()
    other = generate_node_identity()
    registry = P2PIdentityRegistry(str(path), {trusted["public_key"]})

    def enroll(identity):
        challenge = registry.create_challenge()
        signature = sign_enrollment_challenge(
            identity["private_key"], challenge["challenge_id"], challenge["nonce"],
            identity["node_id"],
        )
        return registry.enroll(identity["public_key"], signature, challenge["challenge_id"])

    enroll(trusted)
    enroll(other)
    database = sqlite3.connect(path)
    database.execute(
        "INSERT INTO p2p_nodes(node_id, public_key, trust_level, enrolled_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("ee" * 32, "not-returned", "token=supersecretvalue", 1_700_000_000, 1_700_000_000),
    )
    database.execute(
        "INSERT INTO p2p_nodes(node_id, public_key, trust_level, enrolled_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("not-a-node", "hidden-public", "token=supersecretvalue", 1_700_000_000, 1_700_000_000),
    )
    database.commit()
    before = database.execute("SELECT COUNT(*) FROM p2p_nodes").fetchone()[0]
    schema = database.execute("SELECT sql FROM sqlite_master ORDER BY sql").fetchall()
    database.close()
    monkeypatch.setenv(admin_centre.IDENTITY_DB_ENV, str(path))

    anonymous = client.get("/admin/network/status")
    assert anonymous.status_code == 401
    response = client.get("/admin/network/status", headers=_headers("viewer"))
    assert response.status_code == 200
    health = response.json()["peer_health"]
    assert health["status"] == "enrolled_registry"
    assert health["liveness"] == "not_observed"
    assert health["peer_count"] == 3
    assert health["trusted_count"] == 1
    assert health["public_untrusted_count"] == 1
    assert health["unclassified_count"] == 1
    assert health["omitted_count"] == 1
    assert health["nodes_truncated"] is False
    assert {node["node_id"] for node in health["nodes"]} == {
        trusted["node_id"], other["node_id"], "ee" * 32,
    }
    assert response.json()["peer_telemetry"] == "identity_enrollment_not_live_sessions"
    assert "supersecretvalue" not in response.text
    assert "not-a-node" not in response.text
    assert trusted["public_key"] not in response.text
    assert trusted["private_key"] not in response.text
    assert other["public_key"] not in response.text
    assert "hidden-public" not in response.text
    assert "public_key" not in response.text
    assert str(path) not in response.text
    overview = client.get("/admin/overview", headers=_headers("viewer")).json()
    assert overview["peer_health"] == "enrolled_registry"
    assert overview["enrolled_peer_count"] == 3
    denied = client.get("/admin/paper/status", headers=_headers("viewer"))
    assert denied.status_code == 403
    database = sqlite3.connect(path)
    assert database.execute("SELECT COUNT(*) FROM p2p_nodes").fetchone()[0] == before
    assert database.execute("SELECT sql FROM sqlite_master ORDER BY sql").fetchall() == schema
    database.close()


def test_alert_history_ack_survives_memory_clear(client):
    first = client.get("/admin/alerts", headers=_headers("operator")).json()
    alert_id = first["active"][0]["alert_id"]
    assert client.post(f"/admin/alerts/{alert_id}/ack", headers=_headers("viewer")).status_code == 403
    response = client.post(f"/admin/alerts/{alert_id}/ack", headers=_headers("operator"))
    assert response.status_code == 200
    admin_centre.ALERTS.clear()
    after = client.get("/admin/alerts", headers=_headers("viewer")).json()
    assert any(a["alert_id"] == alert_id for a in after["acknowledged"])
    assert all(a["alert_id"] != alert_id for a in after["active"])


def test_corrupt_alert_history_fails_closed(client, tmp_path):
    path = tmp_path / "alerts.sqlite3"
    path.write_bytes(b"foreign-or-corrupt")
    response = client.get("/admin/alerts", headers=_headers("viewer"))
    body = response.json()
    assert body["reason"] == "alert_store_unavailable"
    assert body["durable"] is False
    assert body["active"] is None and body["acknowledged"] is None
    assert body["active_count"] is None and body["acknowledged_count"] is None
    assert path.read_bytes() == b"foreign-or-corrupt"
    assert str(path) not in response.text


def test_blank_alert_store_path_is_not_configured(client, monkeypatch):
    monkeypatch.setenv(admin_centre.ALERT_DB_ENV, ":memory:")
    response = client.get("/admin/alerts", headers=_headers("viewer"))
    assert response.json()["reason"] == "not_configured"
    assert response.json()["active"] is None


def test_local_node_health_is_a_real_bounded_loopback_probe_and_strips_identity(client, monkeypatch):
    monkeypatch.setenv("OSIRIS_P2P_LOCAL_HEALTH_PORT", "9090")
    from osiris_api import p2p_local_health
    sample = {
        "status": "alive", "peer_id": "sensitive-node-identity", "ready": True,
        "uptime_seconds": 123, "connected_peers": 5,
        "bootstrap_peers_connected": 2, "bootstrap_peers_total": 3,
        "relay_configured": True, "relay_reservation_active": True,
        "relays_total": 1, "relay_reservations": 1,
        "private_key": "never-appear",
    }
    with patch.object(p2p_local_health, "HTTPConnection") as client_conn:
        http = client_conn.return_value
        http.getresponse.return_value.status = 200
        http.getresponse.return_value.read.return_value = json.dumps(sample).encode()
        response = client.get("/admin/network/status", headers=_headers("viewer"))
        assert response.status_code == 200
        client_conn.assert_called_once_with("127.0.0.1", 9090, timeout=0.3)
        http.request.assert_called_once_with("GET", "/health")
        http.getresponse.return_value.read.assert_called_once_with(4097)
        http.close.assert_called_once()
    node = response.json()["local_live_node"]
    assert node["status"] == "observed"
    assert node["connected_peers"] == 5
    assert node["scope"] == "one_local_node_not_network_total"
    assert node["authenticated_or_attested"] is False
    assert response.json()["peer_telemetry"] == "local_node_loopback_health"
    assert "sensitive-node-identity" not in response.text
    assert "never-appear" not in response.text


@pytest.mark.parametrize("bad_port", ["", "-1", "0", "65536", "localhost:9090", "99999", " 9090", "09090"])
def test_local_node_probe_rejects_unsafe_port_configuration(client, monkeypatch, bad_port):
    monkeypatch.setenv("OSIRIS_P2P_LOCAL_HEALTH_PORT", bad_port)
    from osiris_api import p2p_local_health
    with patch.object(p2p_local_health, "HTTPConnection") as connection:
        response = client.get("/admin/network/status", headers=_headers("viewer"))
        connection.assert_not_called()
    assert response.json()["local_live_node"]["status"] == "unavailable"
    assert response.json()["local_live_node"]["reason"] == "invalid_port"


def test_local_node_probe_unavailable_fails_closed_without_fabricating_peers(client, monkeypatch):
    monkeypatch.setenv("OSIRIS_P2P_LOCAL_HEALTH_PORT", "9090")
    from osiris_api import p2p_local_health
    with patch.object(p2p_local_health, "HTTPConnection") as connection:
        connection.return_value.request.side_effect = OSError("private-host-detail")
        response = client.get("/admin/network/status", headers=_headers("viewer"))
    assert response.status_code == 200
    body = response.json()
    assert body["local_live_node"] == {
        "status": "unavailable", "reason": "health_unreachable_or_invalid",
        "source": "loopback_local_node",
    }
    assert body["peer_health"]["peer_count"] is None
    assert "private-host-detail" not in response.text
