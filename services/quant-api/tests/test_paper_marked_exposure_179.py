"""A lower buy price cannot hide a fresh mark-up from position and gross caps.

Live broker routing stays disabled. Resting buys stay reserved at their limits.
A missing or stale mark is not treated as a higher valuation.
"""
import pytest

from osiris_api.paper_execution import (
    apply_paper_fill,
    execution_context,
    initialize_paper_account,
    mark_to_market,
    reconcile,
    recover,
    set_kill_switch,
    snapshot,
    submit_paper_order,
)
from osiris_api.risk import RiskLimits


NOW = "2026-11-11T16:00:00+00:00"
LATER = "2026-11-11T16:03:00+00:00"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "marked-exposure.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(path))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    return path


def _init(**limits):
    initialize_paper_account("1000", RiskLimits(
        max_order_notional=limits.pop("max_order_notional", 10_000),
        max_position_notional=limits.pop("max_position_notional", 10_000),
        max_daily_loss=limits.pop("max_daily_loss", 10_000),
        max_gross_exposure=limits.pop("max_gross_exposure", 10_000),
    ), **limits)


def _order(key, **overrides):
    payload = dict(
        client_order_id=key, symbol="BTC", side="BUY", quantity="1", price="100",
        fee="0", observed_at=NOW, now=NOW,
    )
    payload.update(overrides)
    return submit_paper_order(**payload)


def test_lower_buy_price_cannot_hide_a_marked_up_position(db):
    _init(max_position_notional=150, max_gross_exposure=10_000)
    assert _order("open")["status"] == "filled"
    mark_to_market({"BTC": "140"}, observed_at=NOW, now=NOW)
    blocked = _order("add", quantity="0.2", price="100")
    assert blocked["status"] == "risk_rejected"
    assert blocked["reason"] == "max_position_notional_exceeded"
    assert blocked["fill"] is None
    assert blocked["live_execution"] is False
    assert blocked["broker_order_submitted"] is False
    state = snapshot()
    assert state["cash"] == "900"
    assert state["fill_count"] == 1
    assert state["positions"] == [{"symbol": "BTC", "quantity": "1", "average_cost": "100"}]
    context = execution_context(symbol="BTC", price="100", now=NOW)
    assert context["buy_exposure_price"] == "140"
    assert context["current_position_notional"] == "140"
    assert context["current_gross_exposure"] == "140"
    assert context["live_execution"] is False
    resumed = recover()["snapshot"]
    assert resumed["cash"] == "900"
    assert resumed["fill_count"] == 1
    assert reconcile()["passed"] is True
    replay = _order("add", quantity="0.2", price="100")
    assert replay["status"] == "idempotent_replay"
    assert snapshot()["fill_count"] == 1


def test_lower_buy_price_cannot_hide_a_marked_up_gross_book(db):
    _init(max_position_notional=10_000, max_gross_exposure=150)
    assert _order("open")["status"] == "filled"
    mark_to_market({"BTC": "140"}, observed_at=NOW, now=NOW)
    blocked = _order("add", quantity="0.2", price="100")
    assert blocked["reason"] == "exposure_budget_exceeded"
    assert blocked["fill"] is None
    assert snapshot()["cash"] == "900"
    assert snapshot()["fill_count"] == 1
    assert reconcile()["passed"] is True


def test_fresh_mark_projects_a_new_buy_even_with_no_inventory(db):
    _init(max_position_notional=150, max_gross_exposure=150)
    mark_to_market({"BTC": "200"}, observed_at=NOW, now=NOW)
    blocked = _order("open", quantity="1", price="100")
    assert blocked["reason"] == "max_position_notional_exceeded"
    assert blocked["fill"] is None
    assert snapshot()["cash"] == "1000"
    assert snapshot()["positions"] == []
    assert snapshot()["fill_count"] == 0
    context = execution_context(symbol="BTC", price="100", now=NOW)
    assert context["buy_exposure_price"] == "200"
    assert context["current_position_notional"] == "0"
    assert reconcile()["passed"] is True


def test_mark_inside_the_cap_still_lets_the_buy_fill(db):
    _init(max_position_notional=200, max_gross_exposure=200)
    assert _order("open")["status"] == "filled"
    mark_to_market({"BTC": "110"}, observed_at=NOW, now=NOW)
    filled = _order("add", quantity="0.5", price="100")
    assert filled["status"] == "filled"
    assert filled["live_execution"] is False
    assert snapshot()["positions"] == [{"symbol": "BTC", "quantity": "1.5", "average_cost": "100"}]
    assert snapshot()["cash"] == "850"
    assert snapshot()["fill_count"] == 2
    assert reconcile()["passed"] is True


def test_lower_fresh_mark_does_not_inflate_exposure(db):
    _init(max_position_notional=150, max_gross_exposure=150)
    assert _order("open")["status"] == "filled"
    mark_to_market({"BTC": "90"}, observed_at=NOW, now=NOW)
    filled = _order("add", quantity="0.4", price="100")
    assert filled["status"] == "filled"
    assert snapshot()["fill_count"] == 2
    context = execution_context(symbol="BTC", price="100", now=NOW)
    assert context["buy_exposure_price"] == "100"
    assert context["current_position_notional"] == "140"
    assert reconcile()["passed"] is True


def test_stale_higher_mark_does_not_replace_the_order_price(db):
    _init(max_position_notional=150, max_gross_exposure=150, max_market_age_seconds="60")
    assert _order("open")["status"] == "filled"
    mark_to_market({"BTC": "200"}, observed_at=NOW, now=NOW)
    filled = _order("add", quantity="0.2", price="100", observed_at=LATER, now=LATER)
    assert filled["status"] == "filled"
    assert snapshot()["fill_count"] == 2
    context = execution_context(symbol="BTC", price="100", now=LATER)
    assert context["buy_exposure_price"] == "100"
    assert context["current_position_notional"] == "120"
    assert reconcile()["passed"] is True


def test_working_fill_rechecks_a_later_mark_up(db):
    _init(max_position_notional=250, max_gross_exposure=250)
    assert _order("open")["status"] == "filled"
    mark_to_market({"BTC": "100"}, observed_at=NOW, now=NOW)
    assert _order("pending", working=True)["status"] == "accepted"
    mark_to_market({"BTC": "180"}, observed_at=NOW, now=NOW)
    with pytest.raises(ValueError, match="max_position_notional_exceeded"):
        apply_paper_fill(
            client_order_id="pending", client_fill_id="fill-1", quantity="1",
            observed_at=NOW, now=NOW,
        )
    state = snapshot()
    assert state["cash"] == "900"
    assert state["fill_count"] == 1
    assert state["open_orders"][0]["leaves_quantity"] == "1"
    mark_to_market({"BTC": "120"}, observed_at=NOW, now=NOW)
    filled = apply_paper_fill(
        client_order_id="pending", client_fill_id="fill-1", quantity="1",
        observed_at=NOW, now=NOW,
    )
    assert filled["status"] == "filled"
    assert snapshot()["cash"] == "800"
    assert snapshot()["positions"][0]["quantity"] == "2"
    assert reconcile()["passed"] is True


def test_reducing_sell_still_flattens_a_marked_up_position(db):
    _init(max_position_notional=150, max_gross_exposure=150)
    assert _order("open")["status"] == "filled"
    mark_to_market({"BTC": "200"}, observed_at=NOW, now=NOW)
    blocked = _order("add", quantity="0.1")
    assert blocked["reason"] == "max_position_notional_exceeded"
    context = execution_context(symbol="BTC", price="100", side="SELL", now=NOW)
    assert context["buy_exposure_price"] == "200"
    assert context["current_position_notional"] == "100"
    sold = _order("flat", side="SELL", quantity="1", price="100")
    assert sold["status"] == "filled"
    assert sold["live_execution"] is False
    assert snapshot()["positions"] == []
    assert snapshot()["cash"] == "1000"
    assert reconcile()["passed"] is True


def test_kill_switch_still_precedes_the_marked_exposure_check(db):
    _init(max_position_notional=150)
    assert _order("open")["status"] == "filled"
    mark_to_market({"BTC": "200"}, observed_at=NOW, now=NOW)
    set_kill_switch(True)
    blocked = _order("add", quantity="0.1")
    assert blocked["reason"] == "kill_switch_active"
    assert blocked["limits_evaluated"] is False
    assert blocked["fill"] is None
    assert snapshot()["fill_count"] == 1
    assert snapshot()["cash"] == "900"
    assert reconcile()["passed"] is True


def test_resting_buy_stays_reserved_at_its_limit(db):
    _init(max_position_notional=10_000, max_gross_exposure=10_000)
    assert _order("pending", working=True)["status"] == "accepted"
    mark_to_market({"BTC": "180"}, observed_at=NOW, now=NOW)
    context = execution_context(symbol="BTC", price="100", now=NOW)
    assert context["buy_exposure_price"] == "180"
    assert context["current_position_notional"] == "100"
    assert context["current_gross_exposure"] == "100"
    assert snapshot()["cash"] == "1000"
    assert reconcile()["passed"] is True
