import pytest
from fastapi.testclient import TestClient
from osiris_api.main import app
from osiris_api.phase11_systems import (
    calibrate_transaction_costs, evaluate_kill_switch, evaluate_model_rollout,
    margin_funding_stress, options_greeks_risk, plan_position_exit,
    reconcile_ledger, simulate_order_lifecycle,
)
client = TestClient(app)

def test_ledger_reconciliation_fails_closed():
    ok = reconcile_ledger([{"asset":"A","quantity":2}], [{"asset":"A","quantity":2}], 100, 100)
    assert ok["new_risk_allowed"] is True
    bad = reconcile_ledger([{"asset":"A","quantity":2}], [{"asset":"A","quantity":1}], 100, 90)
    assert bad["new_risk_allowed"] is False

def test_order_lifecycle_and_cost_calibration():
    state = simulate_order_lifecycle({"quantity":10,"filled_quantity":4,"status":"partially_filled"})
    assert state["remaining_quantity"] == 6
    with pytest.raises(ValueError):
        simulate_order_lifecycle({"quantity":2,"filled_quantity":3})
    costs = calibrate_transaction_costs([{"bucket":"large","realized_cost_bps":8},
                                         {"bucket":"large","realized_cost_bps":12}])
    assert costs["buckets"]["large"]["mean_cost_bps"] == 10

def test_options_margin_and_exit_planning():
    greeks = options_greeks_risk([{"quantity":2,"multiplier":100,"delta":.5}])
    assert greeks["aggregate_greeks"]["delta"] == 100
    assert margin_funding_stress([{"notional":10000}],500,700,360)["status"] == "margin_breach"
    plan = plan_position_exit([{"asset":"A","quantity":1000,"adv_quantity":10000}])
    assert plan["plans"][0]["base_exit_days"] == 1

def test_rollout_and_kill_switch():
    gates = {k:True for k in ("shadow_period_complete","performance_within_tolerance",
        "drift_within_limit","risk_checks_pass","human_approval")}
    assert evaluate_model_rollout(gates)["status"] == "canary_eligible"
    assert evaluate_model_rollout({})["production_deployment_authorized"] is False
    assert evaluate_kill_switch({"feed":False},0,False)["action"] == "BLOCK_NEW_RISK"

def test_phase11_routes_and_registry():
    assert client.get("/platform/modules").json()["count"] == 97
    cases = [
        ("/portfolio/ledger/reconcile", {"positions":[],"broker_positions":[],"cash":0,"broker_cash":0}),
        ("/execution/orders/lifecycle-audit", {"order":{"quantity":1}}),
        ("/research/execution/cost-calibration", {"observations":[{"bucket":"x","realized_cost_bps":2}]}),
        ("/risk/options/greeks-aggregate", {"positions":[]}),
        ("/risk/margin/funding-stress", {"positions":[],"available_funds":100,"maintenance_margin":50}),
        ("/portfolio/liquidity/exit-plan", {"positions":[]}),
        ("/platform/models/rollout-gate", {"metrics":{}}),
        ("/platform/risk/kill-switch/evaluate", {"health":{},"risk_breaches":0,"data_critical":False}),
    ]
    for path,payload in cases:
        response=client.post(path,json=payload)
        assert response.status_code == 200, (path,response.text)


def test_phase11_rejects_malformed_collections_and_inconsistent_states():
    with pytest.raises(ValueError):
        reconcile_ledger(None, [], 0, 0)
    with pytest.raises(ValueError):
        options_greeks_risk(None)
    with pytest.raises(ValueError):
        margin_funding_stress(None, 100, 50)
    with pytest.raises(ValueError):
        plan_position_exit(None)
    with pytest.raises(ValueError):
        simulate_order_lifecycle({"quantity": 10, "filled_quantity": 2, "status": "accepted"})
    with pytest.raises(ValueError):
        simulate_order_lifecycle({"quantity": 10, "filled_quantity": 10, "status": "cancelled"})
    with pytest.raises(ValueError):
        evaluate_kill_switch({"feed": True}, 0, False, manual_halt="false")
