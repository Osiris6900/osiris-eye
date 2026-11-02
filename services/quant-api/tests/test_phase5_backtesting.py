from fastapi.testclient import TestClient
import pytest

from osiris_api.main import app

client = TestClient(app)


def sample_payload():
    return {
        "asset_returns": [
            {"A": 0.0, "B": 0.0},
            {"A": 0.10, "B": -0.05},
            {"A": 0.02, "B": 0.01},
            {"A": -0.10, "B": 0.04},
        ],
        "target_weights": [
            {"A": 0.5, "B": 0.5},
            {"A": 0.5, "B": 0.5},
            {"A": 0.0, "B": 1.0},
            {"A": 0.0, "B": 1.0},
        ],
        "commission_bps": 1.0,
        "spread_bps": 2.0,
        "slippage_bps": 1.0,
        "impact_bps": 1.0,
        "periods_per_year": 252,
        "max_gross_leverage": 1.0,
        "initial_capital": 100000,
    }


def test_phase5_portfolio_backtest_lags_weights_and_charges_costs():
    result = client.post("/research/backtest/portfolio", json=sample_payload())
    assert result.status_code == 200
    data = result.json()
    assert data["periods"] == 3
    assert data["signal_alignment"] == "target_weights[t-1]_applied_to_returns[t]"
    assert data["portfolio_returns"][0] == pytest.approx(0.025 - 0.0005)
    assert data["cost_assumptions_bps"]["all_in"] == 5
    assert data["execution"] == "not_fill_simulated"
    assert len(data["equity_curve"]) == 4


def test_phase5_rejects_leverage_and_universe_mismatch():
    payload = sample_payload()
    payload["target_weights"][0] = {"A": 1.0, "B": 1.0}
    assert client.post("/research/backtest/portfolio", json=payload).status_code == 422
    payload = sample_payload()
    payload["asset_returns"][2] = {"A": 0.01}
    assert client.post("/research/backtest/portfolio", json=payload).status_code == 422


def test_phase5_rejects_nonfinite_returns_and_bad_costs():
    payload = sample_payload()
    payload["asset_returns"][1]["A"] = float("nan")
    assert client.post("/research/backtest/portfolio", json=payload).status_code == 422
    payload = sample_payload()
    payload["slippage_bps"] = -1
    assert client.post("/research/backtest/portfolio", json=payload).status_code == 422
