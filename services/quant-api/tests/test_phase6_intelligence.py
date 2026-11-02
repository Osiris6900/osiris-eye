from fastapi.testclient import TestClient
import pytest

from osiris_api.main import app
from osiris_api.phase6_intelligence import (
    walk_forward_validation, portfolio_risk_diagnostics, execution_intelligence,
    model_validation, macro_intelligence, rank_strategies,
)

client = TestClient(app)


def test_walk_forward_uses_chronological_non_overlapping_test_windows():
    result = walk_forward_validation([0.01] * 12, 6, 2)
    assert result["fold_count"] == 3
    assert result["folds"][0]["train_end"] == result["folds"][0]["test_start"]
    assert result["folds"][1]["test_start"] > result["folds"][0]["test_start"]
    assert result["untouched_final_test_set_used"] is False


def test_advanced_portfolio_risk_and_execution_capacity():
    risk = portfolio_risk_diagnostics({"A": [0.1, -0.1], "B": [0.0, 0.02]},
                                      {"A": 0.5, "B": 0.5})
    assert risk["asset_count"] == 2 and risk["gross_leverage"] == pytest.approx(1)
    cost = execution_intelligence(200, 1000, 2, 0.02, max_participation=0.1)
    assert cost["capacity_status"] == "exceeds_participation_limit"
    assert cost["execution_enabled"] is False


def test_model_validation_flags_drift_and_never_authorizes_deployment():
    result = model_validation([0, 1, 2, 3], [10, 11, 12], 0.9, 0.7)
    assert result["status"] == "review_required"
    assert result["deployment_authorized"] is False


def test_macro_and_strategy_triage_are_transparent():
    assert macro_intelligence(0.1, 0.2, -0.1, 0.3)["regime"] == "stagflationary_pressure"
    ranked = rank_strategies([
        {"name": "A", "oos_sharpe": 1.2, "max_drawdown": -0.1, "turnover": 2, "trials_tested": 1},
        {"name": "B", "oos_sharpe": 1.1, "max_drawdown": -0.05, "turnover": 1, "trials_tested": 1},
    ])
    assert ranked["ranked_candidates"][0]["name"] == "A"
    assert ranked["validated_alpha"] is False


def test_phase6_api_contracts_and_validation():
    assert client.get("/platform/modules").json()["count"] == 57
    assert client.post("/research/validation/walk-forward",
                       json={"returns": [0.01] * 10, "train_size": 5, "test_size": 2}).status_code == 200
    assert client.post("/research/portfolio/advanced-risk",
                       json={"returns": {"A": [0.1, -0.1, 0.02]}, "weights": {"A": 1}}).status_code == 200
    assert client.post("/research/execution/intelligence",
                       json={"quantity": 10, "adv": 1000, "half_spread_bps": 1, "volatility": 0.02}).json()["execution_enabled"] is False
    assert client.post("/research/models/advanced-validation",
                       json={"reference": [1, 2], "current": [1, 2],
                             "reference_accuracy": 0.8, "current_accuracy": 0.8}).json()["deployment_authorized"] is False
    assert client.post("/research/macro/intelligence",
                       json={"inflation_change": 0.1, "rates_change": 0.1,
                             "growth_change": -0.1, "volatility_change": 0.2}).status_code == 200
    assert client.post("/research/strategies/rank",
                       json={"candidates": [{"name": "x", "oos_sharpe": 1,
                             "max_drawdown": -0.1, "turnover": 1}]}).status_code == 200


def test_phase6_rejects_invalid_data():
    with pytest.raises(ValueError):
        walk_forward_validation([0.1, float("nan"), 0.2], 2, 1)
    with pytest.raises(ValueError):
        portfolio_risk_diagnostics({"A": [0.1, 0.2]}, {"B": 1})
    with pytest.raises(ValueError):
        execution_intelligence(1, 0, 1, 0.1)
    with pytest.raises(ValueError):
        rank_strategies([{"name": "bad", "oos_sharpe": float("nan")}])
