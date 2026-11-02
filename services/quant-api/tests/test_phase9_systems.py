import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app
from osiris_api.phase9_systems import (
    aggregate_derivatives_exposure, analyze_counterparty_risk, audit_financial_graph,
    audit_global_instruments, benchmark_performance, estimate_portfolio_capacity,
    process_corporate_actions, simulate_disaster_recovery,
)

client = TestClient(app)


def test_global_coverage_counterparty_and_actions():
    coverage = audit_global_instruments([{"instrument_id":"ESZ6","asset_class":"future",
        "currency":"USD","exchange":"CME","calendar":"CME","timezone":"America/Chicago",
        "contract_multiplier":50,"expiry":"2026-12-18"}])
    assert coverage["new_risk_allowed"] is True
    assert analyze_counterparty_risk([{"counterparty":"Broker A","exposure":30,"role":"broker"}],
                                     100, .2)["status"] == "alert"
    actions = process_corporate_actions([{"instrument_id":"ABC","type":"split","ratio":2,
        "effective_at":"2026-01-01T00:00:00Z"}],
        [{"instrument_id":"ABC","quantity":10,"cost_basis":5}])
    assert actions["aggregate_lot_cost_basis"]["ABC"] == 50
    assert actions["tax_liability_calculated"] is False


def test_capacity_derivatives_graph_and_dr():
    capacity = estimate_portfolio_capacity([{"asset":"A","quantity":1000,"adv_quantity":1000}])
    assert capacity["positions"][0]["estimated_exit_days"] == 10
    greeks = aggregate_derivatives_exposure([{"underlying":"A","quantity":2,"multiplier":100,
        "delta":.5,"gamma":.1,"vega":.2,"theta":-.01,"rho":.03,
        "expiry":"2026-12-18T21:00:00Z"}])
    assert greeks["aggregate_greeks"]["delta"] == 100
    graph = audit_financial_graph([{"id":"issuer"},{"id":"supplier"}],
        [{"source":"issuer","target":"supplier","relationship":"supplies_to",
          "observed_at":"2026-01-01T00:00:00Z"}])
    assert graph["status"] == "pass"
    dr = simulate_disaster_recovery([{"service":"database","observed_rto_seconds":20,
        "rto_target_seconds":30,"observed_rpo_seconds":2,"rpo_target_seconds":5,
        "restore_tested":True}])
    assert dr["status"] == "pass" and dr["production_failover_executed"] is False


def test_benchmark_metrics_and_routes():
    result = benchmark_performance([.1,.0], [.05,.0])
    assert result["active_total_return"] == pytest.approx(.05)
    assert client.get("/platform/modules").json()["count"] == 87
    payloads = [
        ("/platform/instruments/global-coverage", {"instruments":[]}),
        ("/risk/counterparties/concentration", {"exposures":[],"nav":100}),
        ("/portfolio/corporate-actions/audit", {"actions":[],"tax_lots":[]}),
        ("/research/portfolio/capacity", {"positions":[]}),
        ("/research/derivatives/exposure", {"positions":[]}),
        ("/research/knowledge-graph/financial-audit", {"nodes":[],"edges":[]}),
        ("/platform/resilience/disaster-recovery-simulate", {"services":[]}),
        ("/research/performance/benchmark", {"portfolio_returns":[.01,.02],"benchmark_returns":[.01,0]})
    ]
    for path, payload in payloads:
        response = client.post(path, json=payload)
        assert response.status_code == 200, (path, response.text)


def test_phase9_validates_contract_expiry_and_invalid_numeric_inputs():
    valid = audit_global_instruments([{"instrument_id":"F","asset_class":"future",
        "currency":"USD","exchange":"X","calendar":"X","timezone":"UTC",
        "contract_multiplier":1,"expiry":"2026-01-01"}])
    assert valid["status"] == "pass"
    invalid = audit_global_instruments([{"instrument_id":"F","asset_class":"future",
        "currency":"USD","exchange":"X","calendar":"X","timezone":"UTC",
        "contract_multiplier":1,"expiry":"2026-01-01T00:00:00"}])
    assert invalid["status"] == "blocked"
    assert any("expiry" in issue for issue in invalid["issues"])
    assert simulate_disaster_recovery([])["status"] == "blocked"
    with pytest.raises(ValueError):
        analyze_counterparty_risk([], 0)
    with pytest.raises(ValueError):
        benchmark_performance([.1], [.1])
