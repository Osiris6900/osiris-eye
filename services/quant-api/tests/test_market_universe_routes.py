from fastapi.testclient import TestClient

from osiris_api.main import app


client = TestClient(app)


def test_market_asset_class_endpoint_exposes_global_taxonomy():
    response = client.get("/market-data/asset-classes")
    assert response.status_code == 200
    payload = response.json()
    ids = {item["id"] for item in payload["asset_classes"]}
    assert {"equity", "crypto", "forex", "future", "option", "commodity"} <= ids
    assert payload["exchange_connectivity_verified"] is False
    assert payload["live_execution"] is False


def test_instrument_audit_endpoint_validates_without_persisting():
    response = client.post("/market-data/instruments/audit", json={"instruments": [{
        "asset_class": "crypto", "venue": "EXAMPLE", "symbol": "BTC-USD",
        "quote_currency": "USD", "base_currency": "BTC",
        "timezone": "UTC", "session_type": "continuous",
        "tick_size": "0.01", "lot_size": "0.0001"
    }]})
    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted"] == 1
    assert payload["instruments"][0]["instrument_id"] == "crypto:EXAMPLE:BTC-USD"
    assert payload["catalog_persisted"] is False


def test_instrument_audit_endpoint_rejects_missing_batch():
    response = client.post("/market-data/instruments/audit", json={})
    assert response.status_code == 422
