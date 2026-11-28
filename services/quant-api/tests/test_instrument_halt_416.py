"""A stored halt window closes the paper book only inside that interval."""
from __future__ import annotations

from datetime import datetime

import pytest

from osiris_api.instrument_master import InstrumentMasterStore
from osiris_api.paper_execution import (
    LIVE_EXECUTION_ENABLED,
    apply_paper_fill,
    initialize_paper_account,
    mark_to_market,
    snapshot,
    submit_paper_order,
)
from osiris_api.paper_execution_gate import PaperOrderContext, authorize_paper_order
from osiris_api.risk import RiskLimits

OPEN = "2026-11-23T14:30:00+00:00"
INSIDE = "2026-11-23T15:30:00+00:00"
END = "2026-11-23T16:00:00+00:00"
LIMITS = RiskLimits(
    max_order_notional=100_000, max_position_notional=100_000,
    max_daily_loss=100_000, max_gross_exposure=100_000,
)


def _instrument(**changes):
    record = {
        "asset_class": "crypto", "venue": "PAPER", "exchange": "PAPER",
        "symbol": "HALTWIN", "quote_currency": "USD", "currency": "USD",
        "timezone": "UTC", "session_type": "continuous",
        "tick_size": "0.01", "lot_size": "1", "status": "active",
        "trading_hours": {"open": "00:00", "close": "23:59", "timezone": "UTC"},
        "halt_windows": [{"start": "2026-11-23T15:00:00+00:00", "end": END}],
    }
    record.update(changes)
    return record


@pytest.fixture
def book(tmp_path, monkeypatch):
    instruments = tmp_path / "instruments.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "paper.sqlite3"))
    monkeypatch.setenv("OSIRIS_INSTRUMENT_DB_PATH", str(instruments))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    monkeypatch.delenv("OSIRIS_INSTRUMENT_FIXTURE", raising=False)
    store = InstrumentMasterStore(str(instruments))
    store.put(_instrument(), effective_at="2020-01-01T00:00:00Z")
    initialize_paper_account("100000", LIMITS)
    return store


def _buy(when, *, key="order-1", working=False):
    return submit_paper_order(
        client_order_id=key, symbol="HALTWIN", side="BUY", quantity="1", price="100",
        fee="0", observed_at=when, now=when, risk_service_available=True,
        kill_switch_active=False, working=working,
    )


def test_live_execution_stays_disabled():
    assert LIVE_EXECUTION_ENABLED is False


def test_halt_is_half_open_and_a_missing_clock_fails_closed(book):
    assert book.assess("HALTWIN", price="100", quantity="1", as_of=OPEN)["ok"] is True
    inside = book.assess("HALTWIN", price="100", quantity="1", as_of=INSIDE)
    assert inside["ok"] is False
    assert inside["reason"] == "instrument_halted"
    assert book.assess("HALTWIN", price="100", quantity="1", as_of=END)["ok"] is True
    assert book.assess("HALTWIN", price="100", quantity="1")["reason"] == "instrument_halt_unspecified"


def test_invalid_windows_are_rejected(book):
    with pytest.raises(ValueError, match="invalid_halt_window"):
        book.put(_instrument(halt_windows=[]), effective_at="2021-01-01T00:00:00Z")
    with pytest.raises(ValueError, match="invalid_halt_window"):
        book.put(_instrument(halt_windows=[{
            "start": "2026-11-23T15:00:00", "end": END,
        }]), effective_at="2021-01-01T00:00:00Z")
    with pytest.raises(ValueError, match="overlapping_halt_windows"):
        book.put(_instrument(halt_windows=[
            {"start": "2026-11-23T15:00:00Z", "end": "2026-11-23T16:00:00Z"},
            {"start": "2026-11-23T15:30:00Z", "end": "2026-11-23T17:00:00Z"},
        ]), effective_at="2021-01-01T00:00:00Z")


def test_orders_fills_and_marks_stop_inside_the_window(book):
    opened = _buy(OPEN, key="resting", working=True)
    assert opened["state"] == "accepted"
    halted = _buy(INSIDE, key="halted")
    assert halted["status"] == "instrument_rejected"
    assert halted["reason"] == "instrument_halted"
    before = snapshot()
    with pytest.raises(ValueError, match="instrument_halted"):
        apply_paper_fill(
            client_order_id="resting", client_fill_id="halted", quantity="1",
            price="100", observed_at=INSIDE, now=INSIDE,
        )
    with pytest.raises(ValueError, match="instrument_halted"):
        mark_to_market({"HALTWIN": "101"}, observed_at=INSIDE, now=INSIDE)
    assert snapshot()["fill_count"] == 0
    assert snapshot()["positions"] == before["positions"]
    assert snapshot()["cash"] == "100000"
    resumed = _buy(END, key="after")
    assert resumed["status"] == "filled"


def test_pretrade_gate_uses_the_halt_window(book):
    inside = int(datetime.fromisoformat(INSIDE).timestamp() * 1000)
    context = PaperOrderContext(
        quantity=1, price=100, current_position_notional=0, daily_pnl=0,
        observation_time_ms=inside - 1000, decision_time_ms=inside,
        model_approved=True, feed_authenticated=True, feed_entitled=True,
        kill_switch_active=False, symbol="HALTWIN",
    )
    assert authorize_paper_order(context) == (False, "instrument_halted")
    opened = int(datetime.fromisoformat(OPEN).timestamp() * 1000)
    clear = PaperOrderContext(
        quantity=1, price=100, current_position_notional=0, daily_pnl=0,
        observation_time_ms=opened - 1000, decision_time_ms=opened,
        model_approved=True, feed_authenticated=True, feed_entitled=True,
        kill_switch_active=False, symbol="HALTWIN",
    )
    assert authorize_paper_order(clear)[0] is True
