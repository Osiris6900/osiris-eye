import pytest
from fastapi.testclient import TestClient

from osiris_api import market_routes
from osiris_api.main import app


class FakeAdapter:
    name = "test-feed"
    mode = "sandbox"

    def fetch_bars(self, symbol, start, end):
        return [{
            "symbol": symbol,
            "event_time": "2026-11-02T10:00:00Z",
            "available_at": "2026-11-02T10:00:01Z",
            "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100,
        }]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OSIRIS_ENV", "development")
    monkeypatch.delenv("OSIRIS_API_TOKEN", raising=False)
    market_routes.market_data_gateway = market_routes.MarketDataGateway(
        {"test-feed": FakeAdapter()}, {"test-feed": "test-entitlement"}
    )
    return TestClient(app)


def test_provider_status_endpoint(client):
    response = client.get("/market-data/providers")
    assert response.status_code == 200
    payload = response.json()
    assert payload["live_market_data_enabled"] is False
    assert payload["providers"][0]["provider"] == "test-feed"
    assert payload["providers"][0]["available"] is True


def test_historical_bars_endpoint(client):
    response = client.get("/market-data/bars", params={
        "provider": "test-feed", "symbol": "abc",
        "start": "2026-11-02T09:00:00Z", "end": "2026-11-02T11:00:00Z",
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "ABC"
    assert payload["bars_returned"] == 1
    assert payload["live_execution"] is False


def test_historical_bars_fails_closed_for_unknown_provider(client):
    response = client.get("/market-data/bars", params={
        "provider": "missing", "symbol": "ABC",
        "start": "2026-11-02T09:00:00Z", "end": "2026-11-02T11:00:00Z",
    })
    assert response.status_code == 503
    assert response.json()["detail"] == "provider_adapter_not_configured"


def test_historical_bars_rejects_invalid_range(client):
    response = client.get("/market-data/bars", params={
        "provider": "test-feed", "symbol": "ABC",
        "start": "2026-11-02T11:00:00Z", "end": "2026-11-02T09:00:00Z",
    })
    assert response.status_code == 422
    assert response.json()["detail"] == "end_must_be_after_start"


def test_market_routes_require_bearer_token_when_configured(client, monkeypatch):
    monkeypatch.setenv("OSIRIS_API_TOKEN", "secret-test-token")
    response = client.get("/market-data/providers")
    assert response.status_code == 401
    response = client.get(
        "/market-data/providers",
        headers={"Authorization": "Bearer secret-test-token"},
    )
    assert response.status_code == 200


def test_global_coverage_endpoint_reports_real_connector_limits(client):
    response = client.get("/market-data/global-coverage")
    assert response.status_code == 200
    payload = response.json()
    assert payload["global_coverage_complete"] is False
    assert payload["real_time_all_markets_verified"] is False
    assert payload["live_execution"] is False
    assert {item["provider"] for item in payload["implemented_public_connectors"]} >= {
        "binance-public", "stooq-daily", "coinbase-public"
    }
