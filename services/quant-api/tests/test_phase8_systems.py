import pytest
from fastapi.testclient import TestClient
from osiris_api.main import app
from osiris_api.phase8_systems import (
    audit_backtest_integrity, audit_data_lineage, audit_security_resilience,
    catalyst_impact_scenarios, observe_model_health, orchestrate_research_workflow,
    portfolio_risk_governor, reconcile_paper_trades,
)

client = TestClient(app)


def test_lineage_and_backtest_integrity_fail_closed():
    row = {"record_id": "r1", "source": "feed", "license_status": "authorized",
           "event_time": "2026-01-01T10:00:00Z", "available_at": "2026-01-01T10:00:01Z"}
    assert audit_data_lineage([row])["new_risk_allowed"] is True
    assert audit_data_lineage([{**row, "license_status": "unknown"}])["status"] == "blocked"
    evidence = {k: True for k in ("point_in_time_data", "corporate_actions_adjusted",
        "delistings_included", "costs_modeled", "walk_forward_passed",
        "untouched_holdout_passed", "multiple_testing_disclosed")}
    assert audit_backtest_integrity(evidence)["integrity"] == "pass"
    evidence["untouched_holdout_passed"] = False
    assert audit_backtest_integrity(evidence)["deployment_authorized"] is False


def test_risk_governor_and_catalyst_scenarios():
    positions = [{"asset": "A", "market_value": 80, "nav": 100},
                 {"asset": "B", "market_value": 20, "nav": 100}]
    result = portfolio_risk_governor(positions, 2, 0.7, 0.1, 0.2)
    assert result["status"] == "blocked"
    scenarios = catalyst_impact_scenarios({"A": .6, "B": .4},
        {"downside": {"A": -.1, "B": -.2}})
    assert scenarios["scenarios"]["downside"]["portfolio_return_impact"] == pytest.approx(-.14)
    assert scenarios["forecast_or_probability"] is False


def test_model_observability_and_paper_reconciliation():
    assert observe_model_health([0, 0, 1, 1], [5, 5, 6, 6])["status"] == "alert"
    result = reconcile_paper_trades(
        [{"order_id": "o1", "asset": "A", "side": "buy", "quantity": 10}],
        [{"order_id": "o1", "quantity": 6, "price": 100}],
        [{"asset": "A", "quantity": 5}])
    assert result["status"] == "breaks_found" and result["position_breaks"]["A"] == 1


def test_security_and_workflow_gates():
    controls = {k: True for k in ("mfa", "least_privilege", "secrets_managed",
        "audit_logging", "backup_restore_tested", "kill_switch_tested")}
    assert audit_security_resilience(controls)["new_risk_allowed"] is True
    controls["kill_switch_tested"] = False
    assert audit_security_resilience(controls)["status"] == "blocked"
    gates = {k: True for k in ("data_lineage_passed", "backtest_integrity_passed",
        "walk_forward_passed", "untouched_holdout_passed", "risk_review_passed",
        "paper_reconciliation_passed", "security_review_passed", "human_approval")}
    assert orchestrate_research_workflow(gates)["live_order_routing_enabled"] is False


def test_phase8_routes_and_registry():
    assert client.get("/platform/modules").json()["count"] == 95
    row = {"record_id": "r1", "source": "feed", "license_status": "authorized",
           "event_time": "2026-01-01T10:00:00Z", "available_at": "2026-01-01T10:00:01Z"}
    assert client.post("/platform/data/lineage-audit", json={"records": [row]}).status_code == 200
    assert client.post("/research/backtest/integrity-audit", json={"evidence": {
        k: True for k in ("point_in_time_data", "corporate_actions_adjusted", "delistings_included",
        "costs_modeled", "walk_forward_passed", "untouched_holdout_passed",
        "multiple_testing_disclosed")}}).status_code == 200
    assert client.post("/risk/governor/evaluate", json={"positions": [
        {"asset": "A", "market_value": 10, "nav": 100}], "max_gross_exposure": 2,
        "max_single_name_weight": 1, "current_drawdown": 0, "max_drawdown": .2}).status_code == 200
    assert client.post("/research/catalysts/impact-scenarios", json={
        "weights": {"A": 1}, "scenarios": {"base": {"A": 0.01}}}).status_code == 200
    assert client.post("/research/models/observability", json={
        "reference": [1, 2], "current": [1, 2]}).status_code == 200
    assert client.post("/paper/trading/reconcile", json={
        "orders": [], "fills": [], "positions": []}).status_code == 200
    assert client.post("/platform/security/resilience-audit", json={"controls": {
        k: True for k in ("mfa", "least_privilege", "secrets_managed", "audit_logging",
        "backup_restore_tested", "kill_switch_tested")}}).status_code == 200
    assert client.post("/platform/workflow/research-orchestrate", json={"gates": {
        k: True for k in ("data_lineage_passed", "backtest_integrity_passed",
        "walk_forward_passed", "untouched_holdout_passed", "risk_review_passed",
        "paper_reconciliation_passed", "security_review_passed", "human_approval")}}).status_code == 200


def test_phase8_rejects_invalid_timestamps_and_overfills():
    with pytest.raises(ValueError):
        audit_data_lineage([{"record_id": "x", "source": "s", "license_status": "authorized",
            "event_time": "2026-01-01T10:00:00", "available_at": "2026-01-01T10:00:01Z"}])
    with pytest.raises(ValueError):
        reconcile_paper_trades([{"order_id": "o", "asset": "A", "side": "buy", "quantity": 1}],
            [{"order_id": "o", "quantity": 2, "price": 1}], [])
