from fastapi.testclient import TestClient

from osiris_api.main import app

client = TestClient(app)


def test_health_is_research_only():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "mode": "research_only",
        "live_execution": False,
        "data_store": "sqlite",
        "module_count": 51,
    }


def test_options_iv_endpoint():
    response = client.post(
        "/research/options-iv",
        params=[("target_iv", 0.3), ("peer_ivs", 0.1), ("peer_ivs", 0.2), ("peer_ivs", 0.3)],
    )
    assert response.status_code == 200
    assert response.json()["iv_zscore"] > 0


def test_invalid_research_input_returns_422():
    response = client.post(
        "/research/liquidity", params={"half_spread": -0.1, "slippage": 0.02}
    )
    assert response.status_code == 422


def test_risk_endpoint_never_enables_execution():
    response = client.post(
        "/risk/check",
        json={
            "quantity": 1,
            "price": 100,
            "current_position_notional": 0,
            "daily_pnl": 0,
        },
    )
    assert response.status_code == 200
    assert response.json()["allowed"] is True
    assert response.json()["execution"] == "disabled"



def test_data_audit_blocks_duplicate_and_stale_records():
    record = {
        "dataset": "prices", "record_key": "A-1", "source": "vendor",
        "event_time": "2026-11-02T10:00:00Z",
        "available_at": "2026-11-02T10:00:01Z", "payload": {"close": 100},
    }
    response = client.post("/data/audit", json={
        "records": [record, record], "as_of": "2026-11-02T10:00:10Z",
        "max_age_seconds": 30,
    })
    assert response.status_code == 200
    assert response.json()["status"] == "blocked"
    assert response.json()["new_risk_allowed"] is False


def test_backtest_applies_previous_period_signal_and_costs():
    response = client.post("/research/backtest/simulate", json={
        "signals": [0, 1, 1], "asset_returns": [0, 0.1, 0.1],
        "cost_bps_per_unit_turnover": 10,
    })
    assert response.status_code == 200
    result = response.json()
    assert result["signal_alignment"] == "signal[t-1]_applied_to_return[t]"
    assert result["periods"] == 2
    assert result["strategy_returns"][0] == 0.0
    assert result["strategy_returns"][1] == 0.099
    assert result["execution"] == "not_simulated"


def test_backtest_rejects_misaligned_series():
    response = client.post("/research/backtest/simulate", json={
        "signals": [0, 1], "asset_returns": [0.1, 0.2, 0.3],
    })
    assert response.status_code == 422


def test_all_eight_capability_groups_are_registered():
    ids = {item["id"] for item in client.get("/platform/modules").json()["modules"]}
    assert {"market_data_intelligence", "institutional_backtesting", "multi_agent_research",
            "portfolio_risk_intelligence", "options_intelligence", "global_macro_cross_market",
            "alternative_data_lab", "osiris_command_center"} <= ids


def test_agent_proposal_is_read_only_and_approval_gated():
    response = client.post("/platform/agents/propose-research", json={
        "objective": "Review volatility regime", "roles": ["options", "skeptic"]})
    assert response.status_code == 200
    assert response.json()["permissions"] == "read_only_research"
    assert response.json()["human_approval_required"] is True
    assert response.json()["risk_gate_bypass"] is False


def test_command_center_never_claims_live_connectivity():
    response = client.get("/platform/command-center")
    assert response.status_code == 200
    assert response.json()["broker_connected"] is False
    assert response.json()["order_routing_enabled"] is False


def test_options_surface_duplicate_is_reported():
    p = {"strike": 100, "days_to_expiry": 30, "implied_volatility": 0.2}
    response = client.post("/research/options/surface-audit", json=[p, p])
    assert response.status_code == 200
    assert response.json()["issues"][0]["code"] == "duplicate_expiry_strike"


def test_advanced_systems_9_to_20_api_contracts():
    assert len(client.get("/platform/modules").json()["modules"]) == 51
    book=client.post("/research/microstructure/order-book",json={"bids":[{"price":99,"quantity":10}],"asks":[{"price":101,"quantity":8}]})
    assert book.status_code == 200 and book.json()["spread_bps"] > 0
    sim=client.post("/research/execution/simulate",json={"quantity":10,"arrival_price":100,"fills":[{"quantity":4,"price":101}]})
    assert sim.status_code == 200 and sim.json()["live_execution"] is False
    gov=client.post("/research/models/governance",json={"reference":[0,1,2],"current":[10,11,12],"threshold":1})
    assert gov.status_code == 200 and gov.json()["deployment_authorized"] is False
    graph=client.post("/research/knowledge-graph/audit",json={"nodes":[{"id":"a"}],"edges":[{"source":"a","target":"b","relation":"owns"}]})
    assert graph.status_code == 200 and graph.json()["integrity"] == "degraded"
    strat=client.post("/research/strategies/discover",json={"signals":[[0,1,1,0]],"returns":[0,.01,-.02,.03]})
    assert strat.status_code == 200 and strat.json()["out_of_sample_validated"] is False
    regime=client.post("/research/regime/change-point",json={"values":[0,0,0,0,2,2,2,2],"window":4})
    assert regime.status_code == 200 and regime.json()["change_flag"] is True
    mc=client.post("/research/scenarios/monte-carlo",json={"mean_return":.001,"volatility":.02,"paths":100})
    assert mc.status_code == 200 and mc.json()["forecast_or_guarantee"] is False
    news=client.post("/research/news/audit",json={"items":[{"source":"wire","published_at":"2026-01-01T00:00:00Z","event_time":"2026-01-01T00:00:00Z","headline":"sample","sentiment":.2}]})
    assert news.status_code == 200 and news.json()["sentiment_is_signal"] is False
    sec=client.post("/platform/security/policy-audit",json={"permissions":[{"role":"bot","permissions":["approve_release"]}]})
    assert sec.status_code == 200 and sec.json()["status"] == "review_required"
    attr=client.post("/research/portfolio/attribution",json={"asset_returns":{"A":[.1,.2],"B":[0,.1]},"weights":{"A":.5,"B":.5}})
    assert attr.status_code == 200 and attr.json()["weight_sum"] == 1
    plan=client.post("/platform/compute/plan",json={"tasks":10,"workers":3})
    assert plan.status_code == 200 and plan.json()["tasks_per_worker_ceiling"] == 4
    incident=client.post("/platform/incidents/assess",json={"checks":[{"component":"feed","status":"down","critical":True}]})
    assert incident.status_code == 200 and incident.json()["new_risk_allowed"] is False
