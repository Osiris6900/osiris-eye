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
