from osiris_api.exchange_calendar import ExchangeCalendarStore, aware_utc, normalize_record
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


@pytest.mark.parametrize("zone,day,at", [
    ("Pacific/Kiritimati", "2026-11-04", "2026-11-03T19:30:00Z"),
    ("Asia/Tokyo", "2026-11-04", "2026-11-04T00:30:00Z"),
    ("Pacific/Pago_Pago", "2026-11-03", "2026-11-04T00:30:00Z"),
])
def test_status_uses_venue_local_date(tmp_path, zone, day, at):
    store = ExchangeCalendarStore(str(tmp_path / "calendar.sqlite"))
    store.put(record(timezone=zone, calendar_date=day), "2026-01-01T00:00:00Z")
    result = store.status(" xnys ", at)
    assert result["is_open"] is True
    assert result["venue"] == "XNYS"


def test_western_overnight_can_start_two_utc_dates_before_status(tmp_path):
    store = ExchangeCalendarStore(str(tmp_path / "calendar.sqlite"))
    store.put(record(timezone="Pacific/Pago_Pago", calendar_date="2026-11-02",
                     sessions=[{"open": "18:00", "close": "17:00"}]),
              "2026-01-01T00:00:00Z")
    assert store.status("XNYS", "2026-11-04T00:30:00Z")["is_open"] is True


@pytest.mark.parametrize("holiday", [True, False])
def test_empty_calendar_is_known_closed(tmp_path, holiday):
    store = ExchangeCalendarStore(str(tmp_path / "calendar.sqlite"))
    store.put(record(is_holiday=holiday, sessions=[]), "2026-01-01T00:00:00Z")
    result = store.status("XNYS", "2026-11-03T16:00:00Z")
    assert result["is_open"] is False
    assert result["calendar_data_available"] is True
    assert result["reason"] == "outside_configured_sessions"


def test_yesterdays_closed_session_does_not_establish_todays_calendar(tmp_path):
    store = ExchangeCalendarStore(str(tmp_path / "calendar.sqlite"))
    store.put(record(), "2026-01-01T00:00:00Z")
    result = store.status("XNYS", "2026-11-04T16:00:00Z")
    assert result["calendar_data_available"] is False
    assert result["reason"] == "calendar_data_unavailable"


def test_in_memory_calendar_survives_between_operations():
    store = ExchangeCalendarStore(":memory:")
    store.put(record(), "2026-01-01T00:00:00Z")
    assert store.get("XNYS", "2026-11-03") is not None
    assert store.status("XNYS", "2026-11-03T15:00:00Z")["is_open"] is True
    assert ExchangeCalendarStore(":memory:").get("XNYS", "2026-11-03") is None


def test_ambiguous_dst_time_requires_explicit_resolution():
    with pytest.raises(ValueError, match="ambiguous_due_to_dst"):
        normalize_record(record(calendar_date="2026-11-01",
                                sessions=[{"open": "01:30", "close": "03:00"}]))


def test_status_does_not_trust_ambiguous_legacy_session(tmp_path):
    store = ExchangeCalendarStore(str(tmp_path / "calendar.sqlite"))
    store.put(record(calendar_date="2026-11-01"), "2026-01-01T00:00:00Z")
    with store._connect() as db:
        db.execute("UPDATE calendar_versions SET sessions_json=?",
                   ('[{"open":"01:30","close":"03:00"}]',))
    with pytest.raises(ValueError, match="ambiguous_due_to_dst"):
        store.status("XNYS", "2026-11-01T07:00:00Z")


@pytest.mark.parametrize("clock", ["09:30Z", "09:30+01:00"])
def test_session_wall_times_cannot_override_venue_timezone(clock):
    with pytest.raises(ValueError, match="local_wall_times"):
        normalize_record(record(sessions=[{"open": clock, "close": "16:00"}]))


@pytest.mark.parametrize("zone", ["/etc/passwd", "../UTC", ""])
def test_invalid_timezone_paths_raise_validation_error(zone):
    with pytest.raises(ValueError, match="iana_timezone"):
        normalize_record(record(timezone=zone))


@pytest.mark.parametrize("stamp", ["", 1, False, [], {}])
def test_invalid_calendar_timestamp_cannot_silently_become_now(stamp):
    with pytest.raises(ValueError, match="timezone_aware"):
        aware_utc(stamp)
