from fastapi.testclient import TestClient
import pytest

from osiris_api.main import app
from osiris_api.phase7_market_intelligence import (
    cross_asset_alpha, options_flow_intelligence, liquidity_forecast,
    detect_market_anomalies, offline_rl_evaluation, trade_surveillance,
)

client = TestClient(app)


def test_cross_asset_alpha_scans_lags_without_claiming_alpha():
    result = cross_asset_alpha({"A": [0.1, 0.2, -0.1, 0.05, 0.03],
                                "B": [0.0, 0.1, 0.2, -0.1, 0.05]}, 1)
    assert len(result["relationships"]) == 3
    assert result["hypotheses_only"] is True


def test_options_flow_aggregates_signed_premium_and_flags():
    result = options_flow_intelligence([
        {"side": "buy", "option_type": "call", "premium": 100, "volume": 10, "unusual_flag": True},
        {"side": "sell", "option_type": "put", "premium": 40, "volume": 5},
    ])
    assert result["call_premium"] == 100
    assert result["put_premium"] == -40
    assert len(result["unusual_activity_flags"]) == 1


def test_liquidity_and_anomaly_diagnostics():
    liq = liquidity_forecast([2, 2, 3, 2], [100, 110, 90, 80], [0.1, 0.1, 0.2, 0.1])
    assert liq["forecast_calibrated"] is False
    anomaly = detect_market_anomalies([0, 0.01, -0.01, 0.02, 0.8],
                                      [100, 110, 90, 105, 10000],
                                      [2, 2, 2, 2, 30], 3)
    assert len(anomaly["anomalies"]) >= 1
    assert anomaly["automatic_trading_action"] is False


def test_offline_rl_and_surveillance_are_review_only():
    rl = offline_rl_evaluation([
        {"reward": 1, "behavior_probability": 0.5, "target_probability": 0.5},
        {"reward": 2, "behavior_probability": 0.5, "target_probability": 1},
    ])
    assert rl["offline_only"] is True
    assert rl["policy_training_performed"] is False
    surveillance = trade_surveillance([
        {"order_id": "o1", "action": "submit", "timestamp_seconds": 0, "quantity": 10},
        {"order_id": "o1", "action": "cancel", "timestamp_seconds": 0.2, "quantity": 10},
        {"order_id": "o1", "action": "submit", "timestamp_seconds": 0.3, "quantity": 10},
        {"order_id": "o1", "action": "cancel", "timestamp_seconds": 0.4, "quantity": 10},
        {"order_id": "o1", "action": "submit", "timestamp_seconds": 0.5, "quantity": 10},
        {"order_id": "o1", "action": "cancel", "timestamp_seconds": 0.6, "quantity": 10},
    ])
    assert surveillance["human_review_required"] is True
    assert surveillance["enforcement_action_taken"] is False


def test_phase7_api_contracts_and_registry():
    assert client.get("/platform/modules").json()["count"] == 71
    assert client.post("/research/alpha/cross-asset-discovery",
                       json={"returns": {"A": [1, 2, 3, 4], "B": [2, 1, 4, 3]}, "max_lag": 1}).status_code == 200
    assert client.post("/research/options/flow-intelligence",
                       json={"trades": [{"side": "buy", "option_type": "call", "premium": 10, "volume": 2}]}).status_code == 200
    assert client.post("/research/liquidity/forecast",
                       json={"spread_bps": [1, 2, 1], "volume": [10, 20, 15], "volatility": [0.1, 0.2, 0.1]}).status_code == 200
    assert client.post("/research/market/anomalies",
                       json={"returns": [0, 0.1, 0, 0.1], "volumes": [1, 2, 1, 2], "spread_bps": [1, 1, 1, 1]}).status_code == 200
    assert client.post("/research/rl/offline-evaluation",
                       json={"episodes": [{"reward": 1, "behavior_probability": 1, "target_probability": 1}]}).status_code == 200
    assert client.post("/surveillance/trades/analyze",
                       json={"events": [{"order_id": "x", "action": "submit", "timestamp_seconds": 0, "quantity": 1}]}).status_code == 200


def test_phase7_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        cross_asset_alpha({"A": [1, 2], "B": [1, 2]}, 3)
    with pytest.raises(ValueError):
        options_flow_intelligence([{"side": "unknown", "option_type": "call"}])
    with pytest.raises(ValueError):
        liquidity_forecast([1, 2], [1], [0.1, 0.2])
    with pytest.raises(ValueError):
        offline_rl_evaluation([{"reward": 1, "behavior_probability": 0, "target_probability": 1}])
