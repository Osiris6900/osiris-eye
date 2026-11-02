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
