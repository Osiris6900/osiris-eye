from fastapi.testclient import TestClient
import pytest
from osiris_api.main import app

client = TestClient(app)


def test_phase3_registers_twelve_systems_and_contracts():
    assert client.get("/platform/modules").json()["count"] == 107
    assert client.get("/health").json()["live_execution"] is False
    assert client.post("/platform/connectors/contract-audit", json={
        "provider": "demo", "dataset": "bars", "timestamp": "2026-11-02T10:00:00Z",
        "license_status": "authorized"}).json()["adapter_connected"] is False
    replay = client.post("/research/market/replay", json={"events": [
        {"timestamp": "2026-01-02T10:00:01Z", "type": "trade"},
        {"timestamp": "2026-01-02T10:00:00Z", "type": "quote"}]})
    assert replay.json()["events"][0]["type"] == "quote"
    opt = client.post("/research/portfolio/optimize", json={"volatilities": [0.1, 0.2], "max_weight": 0.6})
    assert sum(opt.json()["weights"]) == pytest.approx(1)
    order = client.post("/paper/orders/lifecycle", json={"quantity": 10, "filled_quantity": 4})
    assert order.json()["status"] == "partially_filled" and order.json()["live_order"] is False
    factor = client.post("/research/factors/diagnostics", json={
        "asset_returns": {"A": [0.01, 0.02, -0.01]},
        "factor_returns": {"MKT": [0.01, 0.03, -0.02]}})
    assert factor.status_code == 200 and factor.json()["causal_interpretation"] is False
    greeks = client.post("/research/options/greeks-aggregate", json={"positions": [
        {"quantity": 2, "multiplier": 100, "delta": 0.5, "vega": 0.1}]})
    assert greeks.json()["aggregate_greeks"]["delta"] == 100
    events = client.post("/research/events/calendar-audit", json={"events": [
        {"event_id": "E1", "event_time": "2026-11-02T10:00:00Z", "source": "vendor", "event_type": "earnings"}]})
    assert events.json()["issues"] == []
    manifest = client.post("/research/experiments/manifest", json={
        "dataset_version": "v1", "code_version": "abc", "parameters": {"x": 1},
        "seed": 7, "as_of": "2026-11-02T00:00:00Z"})
    assert manifest.json()["reproducible_manifest"] is True
    stress = client.post("/research/portfolio/stress", json={
        "returns": {"A": [0.01], "B": [0.02]}, "weights": {"A": 0.5, "B": 0.5},
        "shocks": {"A": -0.1, "B": -0.2}})
    assert stress.json()["portfolio_shock_return"] == pytest.approx(-0.15)
    health = client.post("/platform/sre/health", json={"services": [{"service": "feed", "status": "down"}]})
    assert health.json()["new_risk_allowed"] is False
    rbac = client.post("/platform/security/rbac-audit", json={"grants": [
        {"principal": "bot", "permissions": ["disable_risk"]}]})
    assert rbac.json()["status"] == "review_required"
    gate = client.post("/platform/workflow/gate", json={
        "data_quality_passed": True, "walk_forward_passed": True,
        "untouched_oos_passed": True, "paper_trading_passed": True,
        "risk_approval": True, "human_approval": True})
    assert gate.json()["research_workflow_ready_for_review"] is True
    assert gate.json()["live_order_routing_enabled"] is False


def test_phase3_rejects_naive_replay_timestamp_and_bad_optimizer():
    assert client.post("/research/market/replay", json={"events": [
        {"timestamp": "2026-01-01T10:00:00"}]}).status_code == 422
    assert client.post("/research/portfolio/optimize", json={
        "volatilities": [0.1, 0.2], "max_weight": 0.4}).status_code == 422
