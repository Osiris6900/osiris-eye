"""Research intelligence must never imply measured trading opportunities."""
import math
import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app
from osiris_api.neural_field import SentimentResearch
from osiris_api.satellite_arb import compare_routes


def test_route_simulation_is_explicit_and_deterministic():
    a = compare_routes(40.7128, -74.006, 51.5074, -0.1278)
    b = compare_routes(40.7128, -74.006, 51.5074, -0.1278)
    assert a["mode"] == "simulation_only"
    assert a["clock_skew_measured"] is False
    assert a["arbitrage_verified"] is False
    assert a["execution_enabled"] is False
    assert a["modeled_difference_us"] == b["modeled_difference_us"]


@pytest.mark.parametrize("args", [
    (91, 0, 0, 0), (0, 181, 0, 0),
])
def test_invalid_coordinates_fail_closed(args):
    with pytest.raises(ValueError):
        compare_routes(*args)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.1, 1.1])
def test_sentiment_invalid_score_rejected(value):
    engine = SentimentResearch()
    with pytest.raises(ValueError):
        engine.ingest("test", "BTC", value, 1, 1)


def test_sentiment_preserves_sign_and_bounds_history():
    engine = SentimentResearch(capacity=2)
    engine.ingest("test", "BTC", -1, 1, 1)
    engine.ingest("test", "BTC", -0.5, 1, 1)
    engine.ingest("test", "BTC", -0.5, 1, 1)
    result = engine.summary()
    assert result["buffered_signals"] == 2
    assert result["weighted_sentiment"] == -0.5
    assert result["forecast_verified"] is False


def test_dashboard_endpoint_disables_execution():
    client = TestClient(app)
    result = client.get("/api/v1/intelligence/dashboard/intelligence-overview")
    assert result.status_code == 200
    payload = result.json()
    assert payload["execution_enabled"] is False
    assert payload["satellite"]["arbitrage_verified"] is False


def test_no_live_execution_route_exposed():
    paths = set(app.openapi()["paths"])
    assert "/api/v1/intelligence/execute-arb" not in paths
    assert "/api/v1/intelligence/satellite/active-windows" not in paths
