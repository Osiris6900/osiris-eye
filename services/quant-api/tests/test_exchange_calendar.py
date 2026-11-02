from osiris_api.exchange_calendar import ExchangeCalendarStore, normalize_record
import pytest


def record(**overrides):
    item = {"venue": "XNYS", "timezone": "America/New_York",
            "calendar_date": "2026-11-03", "sessions": [{"open": "09:30", "close": "16:00"}],
            "source": "test-fixture"}
    item.update(overrides)
    return item


def test_store_and_point_in_time_versions(tmp_path):
    store = ExchangeCalendarStore(str(tmp_path / "calendar.sqlite"))
    first = store.put(record(), "2026-01-01T00:00:00Z")
    second = store.put(record(sessions=[{"open": "09:30", "close": "13:00"}]),
                       "2026-11-01T00:00:00Z")
    assert store.get("XNYS", "2026-11-03", "2026-10-01T00:00:00Z")["sessions"][0]["close"] == "16:00"
    assert store.get("XNYS", "2026-11-03", "2026-11-02T00:00:00Z")["sessions"][0]["close"] == "13:00"
    assert first["stored"] and second["stored"]


def test_holiday_and_overnight_session():
    assert normalize_record(record(is_holiday=True, sessions=[]))["is_holiday"]
    normalized = normalize_record(record(sessions=[{"open": "18:00", "close": "17:00"}]))
    assert normalized["sessions"][0]["open"] == "18:00"


def test_reject_naive_effective_timestamp_and_timezone():
    store = ExchangeCalendarStore(":memory:")
    with pytest.raises(ValueError, match="timezone_aware"):
        store.put(record(), "2026-01-01T00:00:00")
    with pytest.raises(ValueError, match="iana_timezone"):
        normalize_record(record(timezone="Mars/Olympus"))


def test_reject_overlapping_sessions_and_holiday_sessions():
    with pytest.raises(ValueError, match="overlapping_sessions"):
        normalize_record(record(sessions=[{"open":"09:00","close":"12:00"},
                                          {"open":"11:00","close":"14:00"}]))
    with pytest.raises(ValueError, match="holiday_cannot"):
        normalize_record(record(is_holiday=True))


def test_dst_nonexistent_wall_time_rejected():
    bad = record(calendar_date="2026-03-08", sessions=[{"open":"02:30","close":"04:00"}])
    with pytest.raises(ValueError, match="nonexistent_due_to_dst"):
        normalize_record(bad)


def test_market_status_overnight(tmp_path):
    store = ExchangeCalendarStore(str(tmp_path / "overnight.sqlite"))
    store.put(record(sessions=[{"open":"18:00","close":"17:00"}]),
              "2026-01-01T00:00:00Z")
    result = store.status("XNYS", "2026-11-04T02:00:00Z")
    assert result["is_open"] is True
