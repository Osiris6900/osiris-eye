from fastapi.testclient import TestClient
from osiris_api.main import app
from osiris_api.data_store import ResearchStore

client = TestClient(app)

def test_phase4_registry_and_market_data_contracts():
    assert client.get("/platform/modules").json()["count"] == 87
    records = [{"symbol": "abc", "record_key": "abc-1", "event_time": "2026-11-02T10:00:00Z",
                "available_at": "2026-11-02T10:00:01Z", "bid": 99, "ask": 100, "close": 99.5}]
    audit = client.post("/platform/data/market-batch-audit", json={
        "provider": "demo-feed", "dataset": "bars", "license_status": "authorized", "records": records})
    assert audit.status_code == 200 and audit.json()["ingestion_allowed"] is True
    denied = client.post("/data/market/batch-ingest", json={
        "provider": "unverified", "dataset": "bars", "license_status": "unknown", "records": records})
    assert denied.status_code == 422
    instrument = client.post("/platform/data/instrument-master-audit", json={
        "instruments": [{"symbol": "abc", "asset_class": "equity"}, {"symbol": "ABC", "asset_class": "equity"}]})
    assert instrument.json()["valid"] is False
    fresh = client.post("/platform/data/feed-health", json={
        "last_event_time": "2026-11-02T10:00:00Z", "last_available_at": "2026-11-02T10:00:01Z",
        "as_of": "2026-11-02T10:00:10Z", "max_lag_seconds": 30})
    assert fresh.json()["status"] == "fresh" and fresh.json()["new_risk_allowed"] is True
    stale = client.post("/platform/data/feed-health", json={
        "last_event_time": "2026-11-02T09:00:00Z", "last_available_at": "2026-11-02T09:00:01Z",
        "as_of": "2026-11-02T10:00:10Z", "max_lag_seconds": 30})
    assert stale.json()["new_risk_allowed"] is False

def test_point_in_time_query_excludes_late_published_records(tmp_path):
    store = ResearchStore(str(tmp_path / "pit.sqlite3"))
    store.put(dataset="bars", record_key="old", source="feed", event_time="2026-01-01T10:00:00Z",
              available_at="2026-01-01T10:00:01Z", payload={"close": 10})
    store.put(dataset="bars", record_key="late", source="feed", event_time="2026-01-01T10:00:00Z",
              available_at="2026-01-01T10:05:00Z", payload={"close": 11})
    assert [x["record_key"] for x in store.list_as_of("bars", "2026-01-01T10:01:00Z")] == ["old"]

def test_phase4_rejects_bad_batch_and_future_feed_clock():
    bad = client.post("/platform/data/market-batch-audit", json={
        "provider": "x", "dataset": "bars", "license_status": "authorized",
        "records": [{"symbol": "A", "record_key": "1", "event_time": "2026-01-01T10:00:00",
                     "available_at": "2026-01-01T10:01:00Z"}]})
    assert bad.json()["valid"] is False
    future = client.post("/platform/data/feed-health", json={
        "last_event_time": "2026-01-02T10:00:00Z", "last_available_at": "2026-01-02T10:00:00Z",
        "as_of": "2026-01-01T10:00:00Z"})
    assert future.json()["status"] == "unknown" and future.json()["new_risk_allowed"] is False
