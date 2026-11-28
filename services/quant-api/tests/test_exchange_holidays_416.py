"""Seeded exchange holidays close the paper book. Dates outside coverage fail closed."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from osiris_api.exchange_holiday_calendar import session_early_close, session_holiday_reason, summary
from osiris_api.paper_execution import (
    LIVE_EXECUTION_ENABLED,
    apply_paper_fill,
    initialize_paper_account,
    mark_to_market,
    reconcile,
    snapshot,
    submit_paper_order,
)
from osiris_api.paper_execution_gate import PaperOrderContext, authorize_paper_order
from osiris_api.risk import RiskLimits

LIMITS = RiskLimits(
    max_order_notional=100_000, max_position_notional=100_000,
    max_daily_loss=100_000, max_gross_exposure=100_000,
)


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "paper.sqlite3"))
    monkeypatch.setenv("OSIRIS_INSTRUMENT_DB_PATH", str(tmp_path / "instruments.sqlite3"))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    monkeypatch.delenv("OSIRIS_INSTRUMENT_FIXTURE", raising=False)
    initialize_paper_account("100000", LIMITS)
    return tmp_path


def _buy(symbol, when, *, price, quantity="1", key="order-1", working=False):
    return submit_paper_order(
        client_order_id=key, symbol=symbol, side="BUY", quantity=quantity, price=price,
        fee="0", observed_at=when, now=when, risk_service_available=True,
        kill_switch_active=False, working=working,
    )


def test_live_execution_stays_disabled():
    assert LIVE_EXECUTION_ENABLED is False


def test_sources_cite_the_exchange_documents():
    body = summary()
    urls = {row["url"] for row in body["sources"]}
    assert "https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf" in urls
    assert "https://nsearchives.nseindia.com/content/circulars/CMTR72260.pdf" in urls
    assert any("NYSE-Group-Announces-2024-2025-and-2026" in url for url in urls)
    assert body["bse_seeded"] is False
    assert body["early_closes_seeded"] is True
    assert body["live_calendar_feed"] is False
    counts = {row["venue"]: row["early_close_count"] for row in body["venues"]}
    assert counts == {"NSE": 0, "XNYS": 8}


def test_nse_diwali_does_not_fill_and_a_later_monday_does(book):
    # 2026-11-10 is Diwali-Balipratipada, inside the NSE cash session clock.
    holiday = _buy("RELIANCE", "2026-11-10T04:30:00+00:00", price="100.05", quantity="1")
    assert holiday["status"] == "instrument_rejected"
    assert holiday["reason"] == "exchange_holiday"
    assert snapshot()["cash"] == "100000"
    assert snapshot()["positions"] == []
    opened = _buy("RELIANCE", "2026-11-23T04:30:00+00:00", price="100.05", quantity="1", key="monday")
    assert opened["status"] == "filled"
    assert reconcile()["passed"] is True


def test_nse_fill_and_mark_on_the_holiday_do_not_change_the_book(book):
    opened = _buy(
        "RELIANCE", "2026-11-09T04:30:00+00:00", price="100.05", quantity="2",
        key="resting", working=True,
    )
    assert opened["state"] == "accepted"
    before = snapshot()
    with pytest.raises(ValueError, match="exchange_holiday"):
        apply_paper_fill(
            client_order_id="resting", client_fill_id="holiday", quantity="1",
            price="100.05", observed_at="2026-11-10T04:30:00+00:00", now="2026-11-10T04:30:00+00:00",
        )
    with pytest.raises(ValueError, match="exchange_holiday"):
        mark_to_market(
            {"RELIANCE": "100.10"}, observed_at="2026-11-10T04:30:00+00:00",
            now="2026-11-10T04:30:00+00:00",
        )
    assert snapshot()["positions"] == before["positions"]
    assert snapshot()["fill_count"] == 0
    assert snapshot()["cash"] == "100000"


def test_nse_before_coverage_fails_closed(book):
    # Wednesday 1 May 2024 is inside the clock and outside the seeded NSE year.
    result = _buy("RELIANCE", "2024-05-01T04:30:00+00:00", price="100.05")
    assert result["reason"] == "exchange_calendar_out_of_range"
    assert snapshot()["positions"] == []


def test_nyse_thanksgiving_and_a_date_after_coverage(book):
    holiday = _buy("AAPL", "2026-11-26T15:00:00+00:00", price="100.00")
    assert holiday["reason"] == "exchange_holiday"
    later = _buy("AAPL", "2027-01-04T15:00:00+00:00", price="100.00", key="later")
    assert later["reason"] == "exchange_calendar_out_of_range"
    assert snapshot()["cash"] == "100000"


def test_continuous_book_ignores_the_equity_holiday(book):
    result = _buy("BTC", "2026-11-10T04:30:00+00:00", price="100")
    assert result["status"] == "filled"


def test_january_15_is_not_a_holiday_before_its_circular():
    day = date(2026, 1, 15)
    before = datetime(2026, 1, 11, tzinfo=timezone.utc)
    after = datetime(2026, 1, 15, 4, 30, tzinfo=timezone.utc)
    assert session_holiday_reason("NSE", day, before) is None
    assert session_holiday_reason("NSE", day, after) == "exchange_holiday"
    assert session_holiday_reason("BSE", day, after) == "exchange_calendar_unavailable"


def test_nyse_day_after_thanksgiving_closes_at_1300(book):
    # 2026-11-27 is the Friday after Thanksgiving. 17:30Z is 12:30 ET; 18:00Z is 13:00 ET.
    morning = _buy("AAPL", "2026-11-27T17:30:00+00:00", price="100.00", key="morning")
    assert morning["status"] == "filled"
    late = _buy("AAPL", "2026-11-27T18:00:00+00:00", price="100.00", key="late")
    assert late["status"] == "instrument_rejected"
    assert late["reason"] == "outside_trading_hours"
    ordinary = _buy("AAPL", "2026-11-20T20:00:00+00:00", price="100.00", key="ordinary")
    assert ordinary["status"] == "filled"


def test_early_close_blocks_the_fill_and_the_mark(book):
    opened = _buy(
        "AAPL", "2026-11-27T17:30:00+00:00", price="100.00", quantity="2",
        key="resting", working=True,
    )
    assert opened["state"] == "accepted"
    before = snapshot()
    with pytest.raises(ValueError, match="outside_trading_hours"):
        apply_paper_fill(
            client_order_id="resting", client_fill_id="late", quantity="1",
            price="100.00", observed_at="2026-11-27T18:00:00+00:00",
            now="2026-11-27T18:00:00+00:00",
        )
    with pytest.raises(ValueError, match="outside_trading_hours"):
        mark_to_market(
            {"AAPL": "101"}, observed_at="2026-11-27T18:00:00+00:00",
            now="2026-11-27T18:00:00+00:00",
        )
    assert snapshot()["positions"] == before["positions"]
    assert snapshot()["fill_count"] == 0
    assert snapshot()["cash"] == before["cash"]


def test_early_close_follows_the_source_publication_time():
    day = date(2024, 7, 3)
    before = datetime(2023, 11, 9, tzinfo=timezone.utc)
    after = datetime(2024, 7, 3, 17, tzinfo=timezone.utc)
    assert session_early_close("XNYS", day, before) is None
    assert session_early_close("XNYS", day, after) == "13:00"
    published = datetime(2026, 11, 27, 18, tzinfo=timezone.utc)
    assert session_early_close("NSE", date(2026, 11, 27), published) is None
    assert session_early_close("XNYS", date(2026, 7, 3), published) is None


def test_pretrade_gate_uses_the_early_close():
    late = int(datetime.fromisoformat("2026-11-27T18:00:00+00:00").timestamp() * 1000)
    context = PaperOrderContext(
        quantity=1, price=100, current_position_notional=0, daily_pnl=0,
        observation_time_ms=late - 1000, decision_time_ms=late,
        model_approved=True, feed_authenticated=True, feed_entitled=True,
        kill_switch_active=False, symbol="AAPL",
    )
    assert authorize_paper_order(context) == (False, "outside_trading_hours")
    morning = int(datetime.fromisoformat("2026-11-27T17:30:00+00:00").timestamp() * 1000)
    open_session = PaperOrderContext(
        quantity=1, price=100, current_position_notional=0, daily_pnl=0,
        observation_time_ms=morning - 1000, decision_time_ms=morning,
        model_approved=True, feed_authenticated=True, feed_entitled=True,
        kill_switch_active=False, symbol="AAPL",
    )
    assert authorize_paper_order(open_session)[0] is True


def test_pretrade_gate_uses_the_holiday_clock():
    holiday = int(datetime.fromisoformat("2026-11-26T15:00:00+00:00").timestamp() * 1000)
    context = PaperOrderContext(
        quantity=1, price=100, current_position_notional=0, daily_pnl=0,
        observation_time_ms=holiday - 1000, decision_time_ms=holiday,
        model_approved=True, feed_authenticated=True, feed_entitled=True,
        kill_switch_active=False, symbol="AAPL",
    )
    assert authorize_paper_order(context) == (False, "exchange_holiday")
