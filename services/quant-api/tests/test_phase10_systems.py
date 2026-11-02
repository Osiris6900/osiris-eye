import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app
from osiris_api.phase10_systems import (
    allocate_risk_budget, analyze_execution_quality, audit_agent_governance,
    audit_vendor_failover, evaluate_strategy_approval, monitor_portfolio_risk,
    score_alpha_hypotheses, simulate_systemic_stress,
)

client = TestClient(app)


def test_portfolio_risk_and_vendor_fail_closed():
    risk = monitor_portfolio_risk([{"asset": "A", "market_value": 60},
                                   {"asset": "A", "market_value": 10}], 100,
                                  max_name_weight=.5)
    assert risk["status"] == "breach"
    assert risk["new_risk_allowed"] is False
    feeds = audit_vendor_failover([
        {"vendor": "licensed", "authorized": True, "healthy": True, "age_seconds": 5, "price": 100},
        {"vendor": "unauthorized", "authorized": False, "healthy": True, "age_seconds": 1, "price": 100},
    ])
    assert feeds["selected_vendor"] == "licensed"
    blocked = audit_vendor_failover([{"vendor": "x", "authorized": False,
        "healthy": True, "age_seconds": 1, "price": 100}])
    assert blocked["new_risk_allowed"] is False


def test_alpha_allocation_and_agent_governance():
    alpha = score_alpha_hypotheses([{"hypothesis_id": "h1", "oos_sharpe": 2,
        "trials": 10, "untouched_holdout": True, "reproducible": True,
        "walk_forward_pass": True}])
    assert alpha["ranked_hypotheses"][0]["research_eligible"] is True
    allocation = allocate_risk_budget([{"strategy": "s1", "risk_adjusted_score": 2},
        {"strategy": "s2", "risk_adjusted_score": 1}], 10, .4)
    assert sum(x["weight"] for x in allocation["allocations"]) <= .8
    audit = audit_agent_governance([{"agent_id": "a", "role": "research",
        "allowed_tools": ["read"], "human_approval_required": True,
        "risk_gate_bypass": False, "audit_logging": True}])
    assert audit["status"] == "pass"


def test_tca_stress_and_approval_board():
    tca = analyze_execution_quality([{"trade_id": "t1", "filled_quantity": 10,
        "arrival_price": 100, "average_fill_price": 100.1, "side": "buy"}])
    assert tca["mean_implementation_shortfall_bps"] == pytest.approx(10)
    stress = simulate_systemic_stress([{"factor": "equity", "exposure": 100}],
                                      {"equity": -.2}, 100)
    assert stress["stressed_nav"] == pytest.approx(80)
    approval = evaluate_strategy_approval({key: True for key in (
        "data_lineage_pass", "backtest_integrity_pass", "walk_forward_pass",
        "untouched_holdout_pass", "multiple_testing_review_pass", "risk_signoff",
        "paper_trading_pass", "human_approval")})
    assert approval["status"] == "eligible_for_review"
    assert approval["live_deployment_authorized"] is False


def test_phase10_routes_and_registry():
    assert client.get("/platform/modules").json()["count"] == 95
    paths = [
        ("/risk/portfolio/real-time-monitor", {"positions": [], "nav": 100}),
        ("/platform/data/vendor-failover-audit", {"feeds": []}),
        ("/research/alpha/factory-rank", {"experiments": []}),
        ("/portfolio/capital/risk-budget", {"strategies": [{"strategy":"s","risk_adjusted_score":1}], "total_risk_budget":1}),
        ("/platform/agents/governance-audit", {"agents": []}),
        ("/research/execution/tca", {"trades": []}),
        ("/research/risk/systemic-stress", {"exposures": [], "shocks": {}, "nav": 100}),
        ("/platform/strategies/approval-review", {}),
    ]
    for path, payload in paths:
        response = client.post(path, json=payload)
        assert response.status_code == 200, (path, response.text)
