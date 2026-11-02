from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from osiris_api.data_store import ResearchStore
from osiris_api.main import app

client = TestClient(app)


def test_platform_registry_exposes_ten_modules():
    response = client.get("/platform/modules")
    assert response.status_code == 200
    assert response.json()["count"] == 10
    assert response.json()["execution_enabled"] is False


def test_shared_research_api_endpoints():
    assert client.post("/research/microstructure/imbalance",
                       json={"bid_volume": 80, "ask_volume": 20}).json()[
                           "normalized_imbalance"] == pytest.approx(0.6)
    assert sum(client.post("/research/portfolio/weights",
                           json={"volatilities": [0.1, 0.2]}).json()["weights"]) == pytest.approx(1)
    risk = client.post("/research/risk/historical",
                       json={"returns": [-0.01, -0.03, 0.02], "confidence": 0.66})
    assert risk.status_code == 200
    assert risk.json()["expected_shortfall"] >= risk.json()["historical_var"]
    assert client.post("/research/execution/cost",
                       json={"half_spread_bps": 2, "slippage_bps": 1}).json()[
                           "execution"] == "disabled"


def test_point_in_time_store_is_idempotent_and_normalizes_utc(tmp_path):
    store = ResearchStore(str(tmp_path / "research.sqlite3"))
    record = dict(dataset="bars", record_key="ABC-1", source="test",
                  event_time="2026-01-01T10:00:00+05:30",
                  available_at="2026-01-01T10:01:00+05:30",
                  payload={"close": 123.4})
    first = store.put(**record)
    second = store.put(**record)
    assert first["id"] == second["id"]
    assert first["event_time"] == "2026-01-01T04:30:00+00:00"
    assert store.list("bars")[0]["payload"]["close"] == 123.4


def test_point_in_time_store_rejects_naive_timestamps_and_non_json_numbers(tmp_path):
    store = ResearchStore(str(tmp_path / "research.sqlite3"))
    with pytest.raises(ValueError):
        store.put(dataset="bars", record_key="x", source="test",
                  event_time="2026-01-01T10:00:00", available_at="2026-01-01T10:01:00Z",
                  payload={})
    with pytest.raises(ValueError):
        store.put(dataset="bars", record_key="y", source="test",
                  event_time="2026-01-01T10:00:00Z", available_at="2026-01-01T10:01:00Z",
                  payload={"bad": float("nan")})


def test_store_uses_utc_not_machine_local_timezone(tmp_path):
    store = ResearchStore(str(tmp_path / "utc.sqlite3"))
    row = store.put(dataset="macro", record_key="1", source="test",
                    event_time="2026-01-01T10:00:00+05:30",
                    available_at="2026-01-01T10:01:00Z", payload={"value": 1})
    assert datetime.fromisoformat(row["event_time"]).utcoffset() == timezone.utc.utcoffset(None)
