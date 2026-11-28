"""Stored instrument trading hours reject paper orders outside the clock window.

Before this change, RELIANCE filled at 15:00Z. That instant is 20:30 in
Asia/Kolkata, after the stored 15:30 close. The fill is now refused and the
book is unchanged. Weekends and exchange holidays are not part of this window.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from osiris_api.instrument_master import InstrumentMasterStore
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

# Sunday 22 Nov 2026. The clock window does not know that the equity session is shut.
INSIDE_AAPL = "2026-11-22T15:00:00+00:00"  # 10:00 America/New_York
AAPL_OPEN = "2026-11-22T14:30:00+00:00"  # 09:30 America/New_York
AAPL_LAST = "2026-11-22T20:59:00+00:00"  # 15:59 America/New_York
AAPL_CLOSE = "2026-11-22T21:00:00+00:00"  # 16:00 America/New_York
NSE_OPEN = "2026-11-22T03:45:00+00:00"  # 09:15 Asia/Kolkata
NSE_LAST = "2026-11-22T09:59:00+00:00"  # 15:29 Asia/Kolkata
NSE_CLOSE = "2026-11-22T10:00:00+00:00"  # 15:30 Asia/Kolkata
NSE_AFTER = "2026-11-22T15:00:00+00:00"  # 20:30 Asia/Kolkata


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "paper.sqlite3"))
    monkeypatch.setenv("OSIRIS_INSTRUMENT_DB_PATH", str(tmp_path / "instruments.sqlite3"))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    monkeypatch.delenv("OSIRIS_INSTRUMENT_FIXTURE", raising=False)
    initialize_paper_account("100000", RiskLimits(
        max_order_notional=100_000, max_position_notional=100_000,
        max_daily_loss=100_000, max_gross_exposure=100_000,
    ))
    return tmp_path


def _order(symbol, now, *, price="100.00", quantity="1", key="order-1", working=False):
    observed = datetime.fromisoformat(now).timestamp() - 30
    observed_at = datetime.fromtimestamp(observed, timezone.utc).isoformat()
    return submit_paper_order(
        client_order_id=key, symbol=symbol, side="BUY", quantity=quantity, price=price,
        fee="0", observed_at=observed_at, now=now, risk_service_available=True,
        kill_switch_active=False, working=working,
    )


def _ms(stamp: str) -> int:
    return int(datetime.fromisoformat(stamp).timestamp() * 1000)


def test_live_execution_stays_disabled():
    assert LIVE_EXECUTION_ENABLED is False


def test_reliance_after_the_close_does_not_fill(book):
    before = snapshot()
    result = _order("RELIANCE", NSE_AFTER, price="100.05", quantity="2", key="late")
    assert result["status"] == "instrument_rejected"
    assert result["state"] == "rejected"
    assert result["reason"] == "outside_trading_hours"
    assert result["fill"] is None
    after = snapshot()
    assert after["cash"] == before["cash"] == "100000"
    assert after["positions"] == []
    assert after["fill_count"] == 0
    assert reconcile()["passed"] is True


@pytest.mark.parametrize("stamp", [NSE_OPEN, NSE_LAST])
def test_reliance_inside_the_stored_window_can_fill(book, stamp):
    result = _order("RELIANCE", stamp, price="100.05", quantity="1", key=stamp)
    assert result["status"] == "filled"
    assert result["reason"] != "outside_trading_hours"
    assert reconcile()["passed"] is True


def test_reliance_at_the_close_minute_is_outside(book):
    result = _order("RELIANCE", NSE_CLOSE, price="100.05", quantity="1", key="close")
    assert result["reason"] == "outside_trading_hours"
    assert snapshot()["fill_count"] == 0


@pytest.mark.parametrize("stamp", [AAPL_OPEN, AAPL_LAST, INSIDE_AAPL])
def test_aapl_clock_window_accepts_the_open_and_rejects_nothing_inside(book, stamp):
    result = _order("AAPL", stamp, price="100.00", quantity="1", key=stamp)
    assert result["status"] == "filled"


def test_aapl_at_the_close_does_not_fill(book):
    result = _order("AAPL", AAPL_CLOSE, price="100.00", quantity="1", key="aapl-close")
    assert result["reason"] == "outside_trading_hours"
    assert snapshot()["positions"] == []


def test_sunday_inside_the_clock_is_not_a_holiday_check(book):
    """2026-11-22 is a Sunday. The stored hours do not encode that close."""
    result = _order("AAPL", INSIDE_AAPL, price="100.00", quantity="1", key="sunday")
    assert result["status"] == "filled"


def test_fill_and_mark_outside_the_session_do_not_change_the_book(book):
    opened = _order("AAPL", INSIDE_AAPL, price="100.00", quantity="4", key="resting", working=True)
    assert opened["state"] == "accepted"
    before = snapshot()
    with pytest.raises(ValueError, match="outside_trading_hours"):
        apply_paper_fill(
            client_order_id="resting", client_fill_id="late-fill", quantity="1",
            price="100.00", observed_at=AAPL_CLOSE, now=AAPL_CLOSE,
        )
    with pytest.raises(ValueError, match="outside_trading_hours"):
        mark_to_market({"AAPL": "100.00"}, observed_at=AAPL_CLOSE, now=AAPL_CLOSE)
    assert snapshot()["positions"] == before["positions"] == []
    assert snapshot()["fill_count"] == 0
    assert snapshot()["marks"] == []
    assert snapshot()["cash"] == "100000"
    assert reconcile()["passed"] is True


def test_missing_clock_fails_closed_for_an_exchange_session(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_INSTRUMENT_DB_PATH", str(tmp_path / "instruments.sqlite3"))
    monkeypatch.delenv("OSIRIS_INSTRUMENT_FIXTURE", raising=False)
    store = InstrumentMasterStore(str(tmp_path / "instruments.sqlite3"))
    store.ensure_seeded()
    decision = store.assess("AAPL", price="100.00", quantity="1")
    assert decision["ok"] is False
    assert decision["reason"] == "trading_hours_unspecified"


def test_continuous_book_includes_the_last_minute(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_INSTRUMENT_DB_PATH", str(tmp_path / "instruments.sqlite3"))
    monkeypatch.delenv("OSIRIS_INSTRUMENT_FIXTURE", raising=False)
    store = InstrumentMasterStore(str(tmp_path / "instruments.sqlite3"))
    store.ensure_seeded()
    decision = store.assess(
        "BTC", price="100", quantity="1", as_of="2026-11-22T23:59:30+00:00",
    )
    assert decision["ok"] is True
    assert decision["reason"] == "tradable"


def test_version_without_stored_hours_is_not_given_a_session(tmp_path):
    store = InstrumentMasterStore(str(tmp_path / "instruments.sqlite3"))
    store.put({
        "asset_class": "equity", "venue": "NASDAQ", "symbol": "AAPL",
        "quote_currency": "USD", "timezone": "America/New_York",
        "session_type": "exchange", "tick_size": "0.01", "lot_size": "1",
    }, effective_at="2020-01-01T00:00:00Z")
    decision = store.assess(
        "AAPL", price="100.01", quantity="1", as_of="2026-11-22T23:00:00+00:00",
    )
    assert decision["ok"] is True


def test_gate_uses_the_decision_clock_not_the_wall_clock(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_INSTRUMENT_DB_PATH", str(tmp_path / "instruments.sqlite3"))
    monkeypatch.delenv("OSIRIS_INSTRUMENT_FIXTURE", raising=False)
    InstrumentMasterStore(str(tmp_path / "instruments.sqlite3")).ensure_seeded()
    def context(stamp, **changes):
        decision = _ms(stamp)
        base = dict(
            quantity=1, price=100, current_position_notional=0, daily_pnl=0,
            observation_time_ms=decision - 1000, decision_time_ms=decision,
            model_approved=True, feed_authenticated=True, feed_entitled=True,
            kill_switch_active=False, symbol="AAPL",
        )
        base.update(changes)
        return PaperOrderContext(**base)

    assert authorize_paper_order(context(INSIDE_AAPL)) == (True, "approved_for_simulation_only")
    assert authorize_paper_order(context(AAPL_CLOSE)) == (False, "outside_trading_hours")
