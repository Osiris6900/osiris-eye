import sqlite3

import pytest
from fastapi.testclient import TestClient

from osiris_api.data_store import ResearchStore
from osiris_api.main import app

client = TestClient(app)


def test_production_api_fails_closed_without_token(monkeypatch):
    monkeypatch.setenv("OSIRIS_ENV", "production")
    monkeypatch.delenv("OSIRIS_API_TOKEN", raising=False)
    assert client.get("/health").status_code == 200
    response = client.get("/platform/modules")
    assert response.status_code == 503


def test_api_requires_constant_time_bearer_token_when_configured(monkeypatch):
    monkeypatch.setenv("OSIRIS_ENV", "production")
    monkeypatch.setenv("OSIRIS_API_TOKEN", "test-secret-token")
    assert client.get("/platform/modules").status_code == 401
    assert client.get("/platform/modules", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get(
        "/platform/modules", headers={"Authorization": "Bearer test-secret-token"}
    ).status_code == 200


def test_observation_store_rejects_duplicate_keys_without_overwriting(tmp_path):
    store = ResearchStore(str(tmp_path / "research.sqlite3"))
    record = dict(
        dataset="prices", record_key="ABC-1", source="vendor",
        event_time="2026-11-02T10:00:00Z", available_at="2026-11-02T10:00:01Z",
        payload={"close": 100},
    )
    first = store.put(**record)
    with pytest.raises(ValueError, match="immutable"):
        store.put(**{**record, "payload": {"close": 999}})
    assert store.list("prices")[0]["payload"]["close"] == 100
    assert store.list("prices")[0]["id"] == first["id"]


def test_observation_store_rejects_availability_before_event_time(tmp_path):
    store = ResearchStore(str(tmp_path / "research.sqlite3"))
    with pytest.raises(ValueError, match="cannot precede"):
        store.put(
            dataset="prices", record_key="ABC-2", source="vendor",
            event_time="2026-11-02T10:00:02Z", available_at="2026-11-02T10:00:01Z",
            payload={"close": 100},
        )


def test_observation_rows_cannot_be_updated_or_deleted_directly(tmp_path):
    store = ResearchStore(str(tmp_path / "research.sqlite3"))
    row = store.put(
        dataset="prices", record_key="ABC-3", source="vendor",
        event_time="2026-11-02T10:00:00Z", available_at="2026-11-02T10:00:01Z",
        payload={"close": 100},
    )
    with store._connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE observations SET payload_json='{}' WHERE id=?", (row["id"],))
        with pytest.raises(sqlite3.IntegrityError, match="deletion is disabled"):
            connection.execute("DELETE FROM observations WHERE id=?", (row["id"],))


def test_python_risk_engine_rejects_boolean_numeric_values():
    from osiris_api.risk import check_order

    assert check_order(quantity=True, price=10, current_position_notional=0, daily_pnl=0) == (
        False, "invalid_numeric_input"
    )
    assert check_order(quantity=1, price=10, current_position_notional=0, daily_pnl=False) == (
        False, "invalid_numeric_input"
    )
