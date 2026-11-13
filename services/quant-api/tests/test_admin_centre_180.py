"""Issue #180: canonical RBAC, audit log, secrets hygiene and snapshot tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from osiris_api import admin_centre, authorized_feeds
from osiris_api.admin_centre import (
    ALERTS,
    AUDIT_LOG,
    DANGEROUS,
    PERMISSIONS,
    AdminAuth,
    AlertsRegistry,
    AuditLog,
)
from osiris_api.authorized_feeds import (
    FeedAdapterRejection,
    SyntheticAuthorizedAdapter,
)

NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
ACTIONS = sorted({a for grants in PERMISSIONS.values() for a in grants} | set(DANGEROUS))


@pytest.fixture(autouse=True)
def clean_state():
    admin_centre.reset_admin_state()
    yield
    admin_centre.reset_admin_state()


# ---------------------------------------------------------------------------
# RBAC matrix (canonical contract; scripts/check_operator_permissions.py uses
# the same tables, so the offline guard and the API cannot drift)


def test_full_role_action_matrix_matches_contract():
    expected = {
        "viewer": {"read_health", "read_audit"},
        "analyst": {"read_health", "read_audit", "read_research"},
        "operator": {"read_health", "read_audit", "read_research", "ack_alert"},
        "risk_officer": {"read_health", "read_audit", "read_research", "ack_alert",
                         "activate_kill_switch"},
        "security_admin": {"read_health", "read_audit", "read_research", "ack_alert",
                           "revoke_peer"},
    }
    for role, allowed in expected.items():
        for action in ACTIONS:
            assert admin_centre.authorize(role, action, True) is (action in allowed), (
                role, action,
            )


def test_dangerous_actions_denied_for_every_role_even_authenticated():
    for role in PERMISSIONS:
        for action in DANGEROUS:
            assert admin_centre.authorize(role, action, True) is False


def test_unauthenticated_and_unknown_roles_always_denied():
    for action in ACTIONS:
        assert admin_centre.authorize("security_admin", action, False) is False
        assert admin_centre.authorize("root", action, True) is False
        assert admin_centre.authorize(None, action, True) is False


# ---------------------------------------------------------------------------
# AdminAuth token resolution


def _env(tokens: dict) -> dict:
    import json

    return {admin_centre.TOKENS_ENV: json.dumps(tokens)}


def test_auth_resolves_each_role_by_bearer_token():
    tokens = {role: f"token-{role}-123456" for role in PERMISSIONS}
    auth = AdminAuth.from_environment(_env(tokens))
    assert auth.configured
    for role in PERMISSIONS:
        assert auth.resolve(f"Bearer token-{role}-123456") == role
    assert auth.resolve("Bearer wrong-token-123") is None
    assert auth.resolve("token-viewer-123456") is None  # scheme required
    assert auth.resolve("") is None


def test_auth_fails_closed_on_bad_configuration():
    bad = [
        {},
        {admin_centre.TOKENS_ENV: "not-json"},
        _env({"root": "token-root-123456"}),              # unknown role
        _env({"viewer": "short"}),                        # too short
        _env({"viewer": "same-token-123", "operator": "same-token-123"}),  # shared
        _env({"viewer": 123}),                            # non-string
    ]
    for env in bad:
        auth = AdminAuth.from_environment(env)
        assert not auth.configured, env
        assert auth.resolve("Bearer token-root-123456") is None


# ---------------------------------------------------------------------------
# Audit log and alerts


def test_audit_log_records_chronologically_and_bounds_limit():
    log = AuditLog()
    for index in range(5):
        log.record("viewer", "read_health", "allow", now=NOW + timedelta(seconds=index))
    entries = log.recent(3)
    assert len(entries) == 3
    assert entries[0]["recorded_at"] < entries[-1]["recorded_at"]
    assert entries[0]["schema"] == admin_centre.AUDIT_SCHEMA
    with pytest.raises(ValueError, match="audit_decision_invalid"):
        log.record("viewer", "read_health", "maybe")
    with pytest.raises(ValueError, match="audit_limit_invalid"):
        log.recent(0)


def test_alerts_registry_idempotent_raise_and_ack():
    registry = AlertsRegistry()
    first = registry.raise_alert("ingestion_disabled", "global", "warning", "off", now=NOW)
    again = registry.raise_alert("ingestion_disabled", "global", "warning", "off", now=NOW)
    assert first is again
    assert len(registry.active()) == 1
    assert registry.ack("ingestion_disabled:global", "operator", now=NOW)["status"] == "acknowledged"
    assert registry.ack("ingestion_disabled:global", "operator", now=NOW) is None
    assert registry.ack("missing:alert", "operator", now=NOW) is None
    assert registry.active() == []
    # A re-raised alert after ack starts a new active record.
    registry.raise_alert("ingestion_disabled", "global", "warning", "off", now=NOW)
    assert len(registry.active()) == 1
    with pytest.raises(ValueError, match="alert_severity_invalid"):
        registry.raise_alert("k", "k", "fatal", "m")


# ---------------------------------------------------------------------------
# Secrets hygiene


def test_scan_payload_detects_secret_keys_and_configured_values():
    payload = {
        "ok": "clean",
        "nested": [{"api_token": "x"}, {"deep": {"password_hash": "y"}}],
        "note": "prefix tok-abcdefgh suffix",
    }
    violations = admin_centre.scan_payload(payload, ("tok-abcdefgh",))
    assert "$.nested[0].api_token" in violations
    assert "$.nested[1].deep.password_hash" in violations
    assert "$.note" in violations
    assert admin_centre.scan_payload({"ok": "clean"}, ("tok-abcdefgh",)) == []


# ---------------------------------------------------------------------------
# Snapshots


def test_feeds_status_reports_registry_freshness_and_suspension():
    status = admin_centre.feeds_status(now=NOW)
    assert status["schema"] == admin_centre.FEEDS_SCHEMA
    assert status["adapter_count"] == 1
    adapter = status["adapters"][0]
    assert adapter["adapter"] == "synthetic-authorized"
    assert adapter["quality_review"]["status"] == "passed"
    assert adapter["quality_review"]["fresh"] is True
    assert adapter["suspended"] is False
    authorized_feeds.suspend_adapter("synthetic-authorized")
    suspended = admin_centre.feeds_status(now=NOW)
    assert suspended["suspended_count"] == 1
    assert suspended["adapters"][0]["suspended"] is True


def test_feeds_status_flags_stale_review():
    stale = admin_centre.feeds_status(now=NOW + timedelta(days=31))
    assert stale["stale_review_count"] == 1
    assert stale["adapters"][0]["quality_review"]["fresh"] is False


def test_suspended_adapter_refused_before_fetch():
    class SpyAdapter:
        name = "synthetic-authorized"

        def fetch(self, *, now):  # pragma: no cover - must never run
            raise AssertionError("fetch must not be called for suspended adapter")

    authorized_feeds.suspend_adapter("synthetic-authorized")
    with pytest.raises(FeedAdapterRejection, match="adapter_suspended"):
        authorized_feeds.ingest_adapter_ticks(
            SpyAdapter(), instruments=["SYNTH-PERP"], now=NOW
        )


def test_suspend_unknown_adapter_fails_closed():
    with pytest.raises(FeedAdapterRejection, match="adapter_not_registered"):
        authorized_feeds.suspend_adapter("unknown-adapter")


def test_missing_identity_registry_is_not_created(tmp_path):
    path = tmp_path / "missing" / "nodes.sqlite3"
    health = admin_centre.read_enrolled_identity_registry(str(path))
    assert health["status"] == "unavailable"
    assert health["reason"] == "registry_file_absent"
    assert health["peer_count"] is None
    assert health["nodes"] is None
    assert not path.exists()
    assert not path.parent.exists()


def test_empty_identity_registry_is_not_a_live_peer_table(tmp_path):
    from osiris_api.p2p_identity import P2PIdentityRegistry

    path = tmp_path / "nodes.sqlite3"
    P2PIdentityRegistry(str(path))
    health = admin_centre.read_enrolled_identity_registry(str(path))
    assert health["status"] == "enrolled_registry"
    assert health["peer_count"] == 0
    assert health["nodes"] == []
    assert health["liveness"] == "not_observed"
    assert health["nodes_truncated"] is False


def test_identity_registry_rejects_unreadable_and_blank_paths(tmp_path):
    blank = admin_centre.read_enrolled_identity_registry("  ")
    memory = admin_centre.read_enrolled_identity_registry(":memory:")
    assert blank["reason"] == "not_configured"
    assert memory["reason"] == "not_configured"
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"not a database")
    before = corrupt.read_bytes()
    health = admin_centre.read_enrolled_identity_registry(str(corrupt))
    assert health["status"] == "unavailable"
    assert health["reason"] == "registry_unreadable"
    assert corrupt.read_bytes() == before
    assert admin_centre.resolve_identity_db_path({}) == admin_centre.DEFAULT_IDENTITY_DB_PATH
    assert admin_centre.resolve_identity_db_path(
        {admin_centre.IDENTITY_DB_ENV: ""}
    ) == ""


def test_identity_registry_truncates_the_node_list(tmp_path):
    import sqlite3

    path = tmp_path / "nodes.sqlite3"
    database = sqlite3.connect(path)
    database.execute(
        "CREATE TABLE p2p_nodes (node_id TEXT PRIMARY KEY, public_key TEXT NOT NULL, "
        "trust_level TEXT NOT NULL, enrolled_at REAL NOT NULL, last_seen_at REAL NOT NULL)"
    )
    database.executemany(
        "INSERT INTO p2p_nodes VALUES (?, ?, ?, ?, ?)",
        [
            (f"{index:064x}", f"key-{index}", "trusted", 1_700_000_000 + index, 1_700_000_100)
            for index in range(admin_centre.PEER_NODE_LIST_LIMIT + 1)
        ],
    )
    database.commit()
    database.close()
    health = admin_centre.read_enrolled_identity_registry(str(path))
    assert health["peer_count"] == admin_centre.PEER_NODE_LIST_LIMIT + 1
    assert health["trusted_count"] == health["peer_count"]
    assert len(health["nodes"]) == admin_centre.PEER_NODE_LIST_LIMIT
    assert health["nodes_truncated"] is True
    assert "key-0" not in str(health)
    network = admin_centre.network_status(identity_registry=health, now=NOW)
    assert network["live_trading_authorized"] is False
    assert network["peer_telemetry"] == "identity_enrollment_not_live_sessions"


def test_network_and_risk_status_flag_disabled_ingestion(monkeypatch):
    monkeypatch.setenv(admin_centre.ENABLE_ENV, "0")  # see test_admin_api_180 fixture note
    network = admin_centre.network_status(now=NOW)
    assert network["schema"] == admin_centre.NETWORK_SCHEMA
    assert network["ingestion_enabled"] is False
    assert network["live_trading_authorized"] is False
    risk = admin_centre.risk_status(network=network, now=NOW)
    kinds = {flag["kind"] for flag in risk["flags"]}
    assert "ingestion_disabled" in kinds
    assert risk["risk_gate"] == "enforced"
    assert risk["live_trading_authorized"] is False


def test_kill_switch_round_trip(monkeypatch):
    monkeypatch.setenv(admin_centre.ENABLE_ENV, "0")  # see test_admin_api_180 fixture note
    assert admin_centre.ingestion_enabled() is False
    admin_centre.set_ingestion_enabled(True)
    assert admin_centre.ingestion_enabled() is True
    admin_centre.set_ingestion_enabled(False)
    assert admin_centre.ingestion_enabled() is False
    with pytest.raises(ValueError, match="kill_switch_state_invalid"):
        admin_centre.set_ingestion_enabled("yes")


def test_evaluate_alerts_mirrors_risk_flags(monkeypatch):
    monkeypatch.setenv(admin_centre.ENABLE_ENV, "0")  # see test_admin_api_180 fixture note
    registry = AlertsRegistry()
    risk = admin_centre.risk_status(now=NOW)
    active = admin_centre.evaluate_alerts(registry, risk, now=NOW)
    kinds = {alert["kind"] for alert in active}
    assert "ingestion_disabled" in kinds
    # Idempotent: re-evaluation does not duplicate alerts.
    again = admin_centre.evaluate_alerts(registry, risk, now=NOW)
    assert len(again) == len(active)


def test_paper_status_uninitialized_is_honest(monkeypatch, tmp_path):
    monkeypatch.setenv("OSIRIS_PAPER_LEDGER_DB", str(tmp_path / "paper.sqlite3"))
    status = admin_centre.paper_status(now=NOW)
    assert status["schema"] == admin_centre.PAPER_SCHEMA
    assert status["initialized"] is False
    assert status["cash"] is None
    assert status["positions"] == []
    assert status["marks_available"] is False
    assert status["live_trading_authorized"] is False


def test_paper_status_reports_real_ledger_state(monkeypatch, tmp_path):
    monkeypatch.setenv("OSIRIS_PAPER_LEDGER_DB", str(tmp_path / "paper.sqlite3"))
    from osiris_api import durable_paper_ledger

    durable_paper_ledger.initialize_account("10000")
    durable_paper_ledger.synthetic_fill(
        "k1", "SYNTH-PERP", "BUY", "2", "100", fee="0"
    )
    status = admin_centre.paper_status(now=NOW)
    assert status["initialized"] is True
    assert status["cash"] == "9800"
    assert status["fill_count"] == 1
    assert status["positions"][0]["symbol"] == "SYNTH-PERP"
    assert status["pnl"]["realized"] == "0"
    assert status["pnl"]["unrealized"] is None  # no marks supplied: honest null
    assert status["cost_basis_gross"] == "200"
    assert status["market_value"] is None
    assert status["open_orders"] is None
    assert status["recent_fills"][0]["symbol"] == "SYNTH-PERP"
    assert status["recent_fills"][0]["side"] == "BUY"
    assert "k1" not in str(status)


def test_synthetic_adapter_smoke_still_admitted_when_not_suspended():
    adapter = SyntheticAuthorizedAdapter(
        seed="admin-180", symbols=["SYNTH-PERP"], start=NOW - timedelta(minutes=15)
    )
    result = authorized_feeds.ingest_adapter_ticks(
        adapter, instruments=["SYNTH-PERP"], now=NOW
    )
    assert result["counts"]["admitted"] > 0


def test_alert_store_missing_file_is_empty_and_not_created(tmp_path):
    from osiris_api.admin_alert_store import record_raised_alerts
    path = tmp_path / "missing" / "alerts.sqlite3"
    result = record_raised_alerts(str(path), [], now=NOW)
    assert result["initialized"] is False
    assert result["active"] == [] and result["acknowledged"] == []
    assert not path.exists() and not path.parent.exists()


def test_alert_store_rejects_foreign_sqlite_without_modifying(tmp_path):
    import sqlite3
    from osiris_api.admin_alert_store import AlertStoreError, record_raised_alerts
    path = tmp_path / "foreign.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE foreign_data (value TEXT)")
    db.execute("INSERT INTO foreign_data VALUES ('keep')")
    db.commit()
    db.close()
    with pytest.raises(AlertStoreError, match="alert_store_unavailable"):
        record_raised_alerts(str(path), [{"kind": "ingestion_disabled", "key": "global", "severity": "warning", "message": "disabled"}], now=NOW)
    db = sqlite3.connect(path)
    assert db.execute("SELECT value FROM foreign_data").fetchone()[0] == "keep"
    db.close()


def _monitoring_network() -> dict:
    node = "ab" * 32
    return {
        "generated_at": NOW.isoformat(),
        "ingestion_enabled": False,
        "peer_health": {
            "status": "enrolled_registry",
            "reason": None,
            "peer_count": 1,
            "trusted_count": 1,
            "public_untrusted_count": 0,
            "unclassified_count": 0,
            "omitted_count": 0,
            "nodes_truncated": False,
            "nodes": [{
                "node_id": node,
                "trust_level": "trusted",
                "enrolled_at": NOW.isoformat(),
                "last_enrollment_observed_at": NOW.isoformat(),
                "public_key": "should-not-leak",
            }],
        },
        "local_live_node": {
            "status": "observed",
            "ready": True,
            "relay_configured": False,
            "relay_reservation_active": False,
            "uptime_seconds": 9,
            "connected_peers": 1,
            "bootstrap_peers_connected": 1,
            "bootstrap_peers_total": 1,
            "relays_total": 0,
            "relay_reservations": 0,
            "peer_id": "sensitive-node-identity",
        },
    }


def test_monitoring_summary_hides_identities_and_denied_paper():
    paper = {
        "source_ledger": "legacy_synthetic",
        "mode": "synthetic-paper-only",
        "initialized": True,
        "cash": "9800",
        "positions": [{"symbol": "SYNTH-PERP", "quantity": "2"}],
        "fill_count": 1,
        "open_orders": None,
        "open_orders_reason": "not_stored",
        "pnl": {"realized": "0", "unrealized": None, "equity": None},
        "idempotency_key": "fill-key-do-not-leak",
    }
    allowed = admin_centre.monitoring_status(
        _monitoring_network(), paper, paper_allowed=True, now=NOW,
    )
    assert allowed["schema"] == admin_centre.MONITORING_SCHEMA
    assert allowed["read_only"] is True
    assert allowed["live_trading_authorized"] is False
    assert allowed["peer_health"]["peer_count"] == 1
    assert allowed["peer_health"]["liveness"] == "not_observed"
    assert "nodes" not in allowed["peer_health"]
    assert allowed["node_status"]["connected_peers"] == 1
    assert allowed["node_status"]["authenticated_or_attested"] is False
    assert allowed["paper"]["status"] == "available"
    assert allowed["paper"]["cash"] == "9800"
    rendered = str(allowed)
    assert "ab" * 32 not in rendered
    assert "should-not-leak" not in rendered
    assert "sensitive-node-identity" not in rendered
    assert "fill-key-do-not-leak" not in rendered
    denied = admin_centre.monitoring_status(
        _monitoring_network(), paper, paper_allowed=False, now=NOW,
    )
    assert denied["paper"]["status"] == "permission_denied"
    assert denied["paper"]["cash"] is None
    assert "9800" not in str(denied["paper"])
    assert "SYNTH-PERP" not in str(denied)


def test_monitoring_hides_unreconciled_and_uninitialized_paper_balances():
    network = {
        "generated_at": "not-a-timestamp",
        "ingestion_enabled": True,
        "peer_health": {"status": "unavailable", "reason": "registry_file_absent"},
        "local_live_node": {"status": "unavailable", "reason": "not_configured", "peer_id": "hidden"},
    }
    failed = admin_centre.monitoring_status(network, {
        "source_ledger": "canonical_execution",
        "mode": "paper_only",
        "initialized": True,
        "cash": "999999",
        "positions": [{"symbol": "BTC-USD"}],
        "fill_count": 4,
        "pnl": {"realized": "50"},
        "reconciliation": {"passed": False, "breaks": ["cash"]},
    }, paper_allowed=True, now=NOW)
    assert failed["generated_at"] == NOW.isoformat()
    assert failed["peer_health"]["peer_count"] is None
    assert failed["node_status"]["connected_peers"] is None
    assert failed["node_status"]["ingestion_enabled"] is True
    assert failed["paper"]["status"] == "reconciliation_unavailable"
    assert failed["paper"]["cash"] is None
    assert "999999" not in str(failed)
    assert "BTC-USD" not in str(failed)
    assert "hidden" not in str(failed)
    uninitialized = admin_centre.monitoring_status(network, {
        "source_ledger": "legacy_synthetic",
        "mode": "synthetic-paper-only",
        "initialized": False,
        "cash": None,
        "positions": [],
        "fill_count": 0,
        "open_orders": None,
        "open_orders_reason": "not_stored",
        "pnl": None,
    }, paper_allowed=True, now=NOW)
    assert uninitialized["paper"]["status"] == "not_initialized"
    assert uninitialized["paper"]["fill_count"] is None
    assert uninitialized["paper"]["cash"] is None
    poisoned = admin_centre.monitoring_status(network, {
        "source_ledger": "legacy_synthetic",
        "mode": "synthetic-paper-only",
        "initialized": True,
        "cash": "token=supersecretvalue",
        "positions": [{"symbol": "SYNTH-PERP"}],
        "fill_count": 1,
        "pnl": {"realized": "0"},
    }, paper_allowed=True, now=NOW)
    assert poisoned["paper"]["status"] == "unavailable"
    assert "supersecretvalue" not in str(poisoned)


def test_operations_lists_node_status_and_hides_denied_paper_positions():
    network = _monitoring_network()
    network["local_live_node"]["version"] = "0.1.0"
    network["local_live_node"]["unsafe version"] = "should-not-leak"
    paper = {
        "source_ledger": "legacy_synthetic",
        "mode": "synthetic-paper-only",
        "initialized": True,
        "positions": [
            {"symbol": "SYNTH-PERP", "quantity": "2", "average_cost": "100", "client_order_id": "fill-key"},
            {"symbol": "FLAT", "quantity": "0", "average_cost": "5"},
        ],
        "kill_switch_active": True,
    }
    allowed = admin_centre.operations_status(network, paper, paper_allowed=True, now=NOW)
    assert allowed["schema"] == admin_centre.OPERATIONS_SCHEMA
    assert allowed["read_only"] is True
    assert allowed["live_trading_authorized"] is False
    enrolled = allowed["nodes"]["nodes"]
    assert allowed["nodes"]["enrolled_count"] == 1
    assert enrolled[0]["status"] == "enrolled"
    assert enrolled[0]["node_id"] == "ab" * 32
    assert enrolled[0]["last_seen_at"] == NOW.isoformat()
    assert enrolled[0]["last_seen_meaning"] == "enrollment_or_reenrollment_not_liveness"
    assert enrolled[0]["version"] is None
    assert enrolled[0]["version_reason"] == "not_recorded"
    assert enrolled[0]["liveness"] == "not_observed"
    local = allowed["nodes"]["local"]
    assert local["status"] == "ready"
    assert local["last_seen_at"] == NOW.isoformat()
    assert local["last_seen_meaning"] == "admin_probe_time_not_node_clock"
    assert local["version"] == "0.1.0"
    assert local["version_reason"] == "reported"
    assert local["authenticated_or_attested"] is False
    trading = allowed["paper_trading"]
    assert trading["status"] == "available"
    assert trading["kill_switch"]["ingestion_state"] == "disabled"
    assert trading["kill_switch"]["paper_execution_state"] == "not_stored"
    assert trading["kill_switch"]["paper_execution_halted"] is None
    assert trading["open_positions"] == [
        {"symbol": "SYNTH-PERP", "quantity": "2", "average_cost": "100"},
    ]
    assert trading["open_position_count"] == 1
    assert trading["daily_loss_gate"]["state"] == "not_stored"
    assert trading["daily_loss_gate"]["limit"] is None
    assert trading["live_trading_authorized"] is False
    rendered = str(allowed)
    assert "should-not-leak" not in rendered
    assert "sensitive-node-identity" not in rendered
    assert "fill-key" not in rendered
    assert "public_key" not in rendered
    denied = admin_centre.operations_status(network, paper, paper_allowed=False, now=NOW)
    assert denied["nodes"]["local"]["version"] == "0.1.0"
    assert denied["paper_trading"]["status"] == "permission_denied"
    assert denied["paper_trading"]["open_positions"] is None
    assert denied["paper_trading"]["daily_loss_gate"]["state"] == "permission_denied"
    assert denied["paper_trading"]["kill_switch"]["ingestion_state"] == "disabled"
    assert denied["paper_trading"]["kill_switch"]["paper_execution_halted"] is None
    assert "SYNTH-PERP" not in str(denied["paper_trading"])
    assert "100" not in str(denied["paper_trading"])


def test_operations_rejects_unsafe_versions_and_unreconciled_positions():
    network = _monitoring_network()
    network["local_live_node"]["version"] = "not a version"
    network["local_live_node"]["ready"] = False
    view = admin_centre.operations_status(network, {
        "source_ledger": "canonical_execution",
        "mode": "paper_only",
        "initialized": True,
        "cash": "999999",
        "positions": [{"symbol": "BTC-USD", "quantity": "1", "average_cost": "10"}],
        "kill_switch_active": True,
        "reconciliation": {"passed": False, "breaks": ["cash"]},
    }, paper_allowed=True, now=NOW)
    assert view["nodes"]["local"]["status"] == "not_ready"
    assert view["nodes"]["local"]["version"] is None
    assert view["nodes"]["local"]["version_reason"] == "not_reported"
    assert "not a version" not in str(view)
    paper = view["paper_trading"]
    assert paper["status"] == "reconciliation_unavailable"
    assert paper["open_positions"] is None
    assert paper["kill_switch"]["paper_execution_halted"] is None
    assert paper["daily_loss_gate"]["state"] == "reconciliation_unavailable"
    assert paper["daily_loss_gate"]["limit"] is None
    assert "BTC-USD" not in str(view)
    assert "999999" not in str(view)


def test_operations_hides_a_daily_loss_gate_that_disagrees_with_its_amounts(monkeypatch):
    def lied():
        return {
            "basis": "realized_pnl", "state": "clear", "reason": None,
            "realized_pnl": "-20", "limit": "10", "live_execution": False,
        }

    monkeypatch.setattr("osiris_api.paper_execution.daily_loss_gate", lied)
    view = admin_centre.operations_status(_monitoring_network(), {
        "source_ledger": "canonical_execution",
        "mode": "paper_only",
        "initialized": True,
        "positions": [{"symbol": "BTC-USD", "quantity": "1", "average_cost": "80"}],
        "kill_switch_active": False,
        "reconciliation": {"passed": True, "breaks": []},
    }, paper_allowed=True, now=NOW)
    gate = view["paper_trading"]["daily_loss_gate"]
    assert gate["state"] == "unavailable"
    assert gate["realized_pnl"] is None
    assert gate["limit"] is None
    assert "-20" not in str(view["paper_trading"]["daily_loss_gate"])
    assert view["paper_trading"]["open_positions"][0]["symbol"] == "BTC-USD"
    assert view["paper_trading"]["kill_switch"]["paper_execution_state"] == "clear"


def test_operations_drops_a_position_book_with_one_invalid_row():
    view = admin_centre.operations_status(_monitoring_network(), {
        "source_ledger": "legacy_synthetic",
        "mode": "synthetic-paper-only",
        "initialized": True,
        "positions": [
            {"symbol": "SYNTH-PERP", "quantity": "1", "average_cost": "10"},
            {"symbol": "bad symbol", "quantity": "1", "average_cost": "10"},
        ],
    }, paper_allowed=True, now=NOW)
    assert view["paper_trading"]["status"] == "available"
    assert view["paper_trading"]["open_positions"] is None
    assert view["paper_trading"]["positions_reason"] == "unavailable"
    assert "bad symbol" not in str(view)
    assert "SYNTH-PERP" not in str(view)


def _trading_limits(*, halted: bool) -> dict:
    return {
        "status": "available",
        "reason": None,
        "live_execution": False,
        "broker_order_submitted": False,
        "kill_switch_active": halted,
        "limits": {
            "max_order_notional": "5000",
            "max_position_notional": "8000",
            "max_daily_loss": "1000",
            "max_gross_exposure": "20000",
        },
    }


def test_paper_trading_view_projects_orders_marks_and_limits(monkeypatch):
    monkeypatch.setattr(
        "osiris_api.paper_execution.read_risk_limits", lambda: _trading_limits(halted=True),
    )
    paper = {
        "generated_at": NOW.isoformat(),
        "source_ledger": "canonical_execution",
        "mode": "paper_only",
        "initialized": True,
        "positions": [{
            "symbol": "BTC-USD", "quantity": "1", "average_cost": "100", "secret": "do-not-leak",
        }],
        "kill_switch_active": True,
        "reconciliation": {"passed": True, "breaks": []},
        "open_orders": [{
            "client_order_id": "open-buy", "symbol": "BTC-USD", "side": "BUY",
            "state": "accepted", "quantity": "1", "price": "50",
            "filled_quantity": "0", "leaves_quantity": "1",
            "risk_decision_id": "should-not-leak",
        }],
        "orders": [{
            "client_order_id": "filled-buy", "symbol": "BTC-USD", "side": "BUY",
            "state": "filled", "quantity": "1", "filled_quantity": "1", "leaves_quantity": "0",
        }],
        "market_value": "110",
        "cost_basis_gross": "100",
        "marks_available": True,
    }
    risk = {
        "market_value": "110",
        "exposure": "110",
        "cost_basis_gross": "100",
        "risk_gate": "enforced",
        "flags": [
            {
                "kind": "paper_execution_halted", "severity": "critical",
                "message": "Paper execution kill switch is active",
            },
            {
                "kind": "feed_quality_review_stale", "severity": "critical",
                "message": "token=supersecretvalue",
            },
        ],
    }
    view = admin_centre.paper_trading_status(paper, risk, ingestion_enabled=False, now=NOW)
    assert view["schema"] == admin_centre.PAPER_TRADING_SCHEMA
    assert view["read_only"] is True
    assert view["status"] == "available"
    assert view["live_trading_authorized"] is False
    assert view["live_execution"] is False
    assert view["broker_order_submitted"] is False
    assert view["positions"] == [{"symbol": "BTC-USD", "quantity": "1", "average_cost": "100"}]
    assert view["open_orders"] == [{
        "client_order_id": "open-buy", "symbol": "BTC-USD", "side": "BUY", "state": "accepted",
        "quantity": "1", "price": "50", "filled_quantity": "0", "leaves_quantity": "1",
    }]
    assert view["recent_orders"][0]["client_order_id"] == "filled-buy"
    assert view["recent_orders"][0]["state"] == "filled"
    assert view["recent_orders_order"] == "client_order_id"
    assert view["marked_exposure"]["status"] == "mark_to_market"
    assert view["marked_exposure"]["market_value"] == "110"
    assert view["marked_exposure"]["exposure"] == "110"
    assert view["marked_exposure"]["exposure_basis"] == "mark_to_market"
    assert view["kill_switch"]["paper_execution_state"] == "halted"
    assert view["kill_switch"]["paper_execution_halted"] is True
    assert view["kill_switch"]["ingestion_enabled"] is False
    assert view["risk_limits"]["status"] == "available"
    assert view["risk_limits"]["limits"]["max_gross_exposure"] == "20000"
    assert view["risk_limits"]["live_execution"] is False
    assert view["risk_gate"] == "enforced"
    assert view["risk_flags"] == [{
        "kind": "paper_execution_halted", "severity": "critical",
        "message": "Paper execution kill switch is active",
    }]
    rendered = str(view)
    assert "should-not-leak" not in rendered
    assert "supersecretvalue" not in rendered
    assert "do-not-leak" not in rendered


def test_paper_trading_view_legacy_omits_orders_and_limits():
    view = admin_centre.paper_trading_status({
        "generated_at": NOW.isoformat(),
        "source_ledger": "legacy_synthetic",
        "mode": "synthetic-paper-only",
        "initialized": True,
        "positions": [{"symbol": "SYNTH-PERP", "quantity": "2", "average_cost": "100"}],
        "open_orders": None,
        "open_orders_reason": "not_stored",
        "market_value": None,
        "cost_basis_gross": "200",
        "marks_available": False,
    }, {
        "market_value": None,
        "exposure": "unavailable_without_marks",
        "cost_basis_gross": "200",
        "risk_gate": "enforced",
        "flags": [],
    }, ingestion_enabled=True, now=NOW)
    assert view["status"] == "available"
    assert view["positions"] == [{"symbol": "SYNTH-PERP", "quantity": "2", "average_cost": "100"}]
    assert view["open_orders"] is None
    assert view["open_orders_reason"] == "not_stored"
    assert view["recent_orders"] is None
    assert view["recent_orders_reason"] == "not_stored"
    assert view["marked_exposure"]["status"] == "unavailable_without_marks"
    assert view["marked_exposure"]["market_value"] is None
    assert view["marked_exposure"]["cost_basis_gross"] == "200"
    assert view["risk_limits"]["status"] == "not_stored"
    assert view["risk_limits"]["limits"] is None
    assert view["kill_switch"]["paper_execution_state"] == "not_stored"


def test_paper_trading_view_hides_unreconciled_books_and_bad_order_ids(monkeypatch):
    monkeypatch.setattr(
        "osiris_api.paper_execution.read_risk_limits", lambda: _trading_limits(halted=False),
    )
    hidden = admin_centre.paper_trading_status({
        "generated_at": NOW.isoformat(),
        "source_ledger": "canonical_execution",
        "mode": "paper_only",
        "initialized": True,
        "cash": "999999",
        "positions": [{"symbol": "BTC-USD", "quantity": "1", "average_cost": "10"}],
        "open_orders": [{
            "client_order_id": "open-buy", "symbol": "BTC-USD", "side": "BUY",
            "state": "accepted", "quantity": "1", "price": "10",
        }],
        "kill_switch_active": False,
        "reconciliation": {"passed": False, "breaks": ["cash"]},
        "market_value": "10",
        "marks_available": True,
    }, {"exposure": "10", "market_value": "10", "risk_gate": "enforced", "flags": []},
        ingestion_enabled=False, now=NOW)
    assert hidden["status"] == "reconciliation_unavailable"
    assert hidden["positions"] is None
    assert hidden["open_orders"] is None
    assert hidden["risk_limits"]["limits"] is None
    assert hidden["marked_exposure"]["market_value"] is None
    assert "BTC-USD" not in str(hidden)
    assert "999999" not in str(hidden)
    poisoned = admin_centre.paper_trading_status({
        "generated_at": NOW.isoformat(),
        "source_ledger": "canonical_execution",
        "mode": "paper_only",
        "initialized": True,
        "kill_switch_active": False,
        "reconciliation": {"passed": True},
        "positions": [{"symbol": "BTC-USD", "quantity": "1", "average_cost": "10"}],
        "open_orders": [{
            "client_order_id": "token=supersecretvalue", "symbol": "BTC-USD", "side": "BUY",
            "state": "accepted", "quantity": "1", "price": "10",
        }],
        "orders": [{
            "client_order_id": "filled-buy", "symbol": "BTC-USD", "side": "BUY",
            "state": "filled", "quantity": "1",
        }],
        "market_value": None,
        "marks_available": False,
        "cost_basis_gross": "10",
    }, {
        "market_value": None, "exposure": "unavailable_without_marks",
        "cost_basis_gross": "10", "risk_gate": "enforced", "flags": [],
    }, ingestion_enabled=False, now=NOW)
    assert poisoned["status"] == "available"
    assert poisoned["open_orders"] is None
    assert poisoned["open_orders_reason"] == "unavailable"
    assert poisoned["recent_orders"][0]["client_order_id"] == "filled-buy"
    assert "supersecretvalue" not in str(poisoned)


def test_paper_trading_view_hides_limits_that_disagree_with_the_halt(monkeypatch):
    monkeypatch.setattr(
        "osiris_api.paper_execution.read_risk_limits", lambda: _trading_limits(halted=False),
    )
    view = admin_centre.paper_trading_status({
        "generated_at": NOW.isoformat(),
        "source_ledger": "canonical_execution",
        "mode": "paper_only",
        "initialized": True,
        "kill_switch_active": True,
        "reconciliation": {"passed": True},
        "positions": [{"symbol": "BTC-USD", "quantity": "1", "average_cost": "80"}],
        "open_orders": [],
        "orders": [],
        "market_value": None,
        "marks_available": False,
    }, {
        "market_value": None, "exposure": "unavailable_without_marks", "risk_gate": "enforced",
        "flags": [],
    }, ingestion_enabled=False, now=NOW)
    assert view["kill_switch"]["paper_execution_state"] == "halted"
    assert view["risk_limits"]["status"] == "unavailable"
    assert view["risk_limits"]["limits"] is None
    assert "5000" not in str(view["risk_limits"])
    assert view["positions"][0]["symbol"] == "BTC-USD"


def test_paper_trading_view_hides_marked_exposure_when_snapshots_disagree():
    view = admin_centre.paper_trading_status({
        "generated_at": NOW.isoformat(),
        "source_ledger": "legacy_synthetic",
        "mode": "synthetic-paper-only",
        "initialized": True,
        "positions": [],
        "open_orders": None,
        "open_orders_reason": "not_stored",
        "market_value": "110",
        "marks_available": True,
        "cost_basis_gross": "100",
    }, {
        "market_value": "999",
        "exposure": "999",
        "cost_basis_gross": "100",
        "risk_gate": "enforced",
        "flags": [],
    }, ingestion_enabled=False, now=NOW)
    assert view["marked_exposure"]["status"] == "unavailable"
    assert view["marked_exposure"]["market_value"] is None
    assert "110" not in str(view["marked_exposure"])
    assert "999" not in str(view)


def test_read_risk_limits_does_not_create_a_missing_database(tmp_path, monkeypatch):
    missing = tmp_path / "absent" / "execution.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(missing))
    from osiris_api.paper_execution import read_risk_limits

    result = read_risk_limits()
    assert result["reason"] == "not_initialized"
    assert result["limits"] is None
    assert result["live_execution"] is False
    assert not missing.exists() and not missing.parent.exists()
