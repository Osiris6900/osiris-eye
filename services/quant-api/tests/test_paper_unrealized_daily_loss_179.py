"""Adverse unrealized loss is an input to the paper daily-loss gate.

Live broker routing stays disabled. Unrealized gains do not offset a realized
loss, and a holding without a fresh mark fails closed instead of counting as
zero loss.
"""
import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app
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
    path = tmp_path / "unrealized-loss.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(path))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    return path


def _init(**limits):
    initialize_paper_account("1000", RiskLimits(
        max_order_notional=10_000,
        max_position_notional=10_000,
        max_daily_loss=limits.pop("max_daily_loss", 20),
        max_gross_exposure=10_000,
    ), **limits)


def _order(key, **overrides):
    payload = dict(
        client_order_id=key, symbol="BTC", side="BUY", quantity="1", price="100",
        fee="0", observed_at=NOW, now=NOW,
    )
    payload.update(overrides)
    return submit_paper_order(**payload)


def test_mark_down_blocks_a_new_buy_while_realized_pnl_is_flat(db):
    _init()
    assert _order("open", quantity="2")["status"] == "filled"
    mark_to_market({"BTC": "90"}, observed_at=NOW, now=NOW)
    blocked = _order("add")
    assert blocked["status"] == "risk_rejected"
    assert blocked["reason"] == "daily_loss_limit_reached"
    assert blocked["fill"] is None
    assert blocked["live_execution"] is False
    assert blocked["broker_order_submitted"] is False
    state = snapshot()
    assert state["cash"] == "800"
    assert state["realized_pnl"] == "0"
    assert state["unrealized_pnl"] == "-20"
    assert state["fill_count"] == 1
    resumed = recover()["snapshot"]
    assert resumed["cash"] == "800"
    assert resumed["fill_count"] == 1
    assert reconcile()["passed"] is True
    replay = _order("add")
    assert replay["status"] == "idempotent_replay"
    assert snapshot()["fill_count"] == 1


def test_lower_stored_mark_is_used_when_the_order_price_is_higher(db):
    _init()
    assert _order("open", quantity="2")["status"] == "filled"
    mark_to_market({"BTC": "90"}, observed_at=NOW, now=NOW)
    blocked = _order("add", price="100")
    assert blocked["reason"] == "daily_loss_limit_reached"
    context = execution_context(symbol="BTC", price="100", now=NOW)
    assert context["adverse_daily_pnl"] == "-20"
    assert context["daily_pnl"] == "-20"
    assert context["daily_loss_ready"] is True
    assert context["daily_loss_basis"] == "realized_plus_adverse_unrealized"
    assert context["live_execution"] is False


def test_loss_inside_the_limit_still_fills(db):
    _init()
    assert _order("open", quantity="2")["status"] == "filled"
    mark_to_market({"BTC": "90.01"}, observed_at=NOW, now=NOW)
    filled = _order("add", price="90.01")
    assert filled["status"] == "filled"
    assert filled["live_execution"] is False
    assert snapshot()["fill_count"] == 2
    assert reconcile()["passed"] is True


def test_unrealized_gain_does_not_offset_a_realized_loss(db):
    _init(max_daily_loss=10)
    assert _order("open", quantity="2")["status"] == "filled"
    mark_to_market({"BTC": "200"}, observed_at=NOW, now=NOW)
    sold = _order("cut", side="SELL", quantity="1", price="50")
    assert sold["status"] == "filled"
    assert sold["fill"]["realized_pnl"] == "-50"
    blocked = _order("again")
    assert blocked["reason"] == "daily_loss_limit_reached"
    assert snapshot()["unrealized_pnl"] == "100"
    flattened = _order("flat", side="SELL", quantity="1", price="50")
    assert flattened["status"] == "filled"
    assert snapshot()["positions"] == []
    assert reconcile()["passed"] is True


def test_unmarked_other_holding_fails_closed_and_a_reducing_sell_still_works(db):
    _init()
    assert _order("eth", symbol="ETH")["status"] == "filled"
    blocked = _order("btc")
    assert blocked["status"] == "risk_rejected"
    assert blocked["reason"] == "unrealized_pnl_unavailable"
    assert blocked["fill"] is None
    assert snapshot()["cash"] == "900"
    assert snapshot()["fill_count"] == 1
    context = execution_context(symbol="BTC", price="100", now=NOW)
    assert context["daily_loss_ready"] is False
    assert context["adverse_daily_pnl"] is None
    assert context["daily_pnl"] == "0"
    sold = _order("flat", symbol="ETH", side="SELL")
    assert sold["status"] == "filled"
    assert snapshot()["positions"] == []
    assert recover()["snapshot"]["cash"] == "1000"
    assert reconcile()["passed"] is True


def test_stale_mark_on_another_holding_is_not_treated_as_known(db):
    _init(max_market_age_seconds="60")
    assert _order("eth", symbol="ETH", observed_at=NOW, now=NOW)["status"] == "filled"
    mark_to_market({"ETH": "50"}, observed_at=NOW, now=NOW)
    blocked = _order("btc", observed_at=LATER, now=LATER)
    assert blocked["reason"] == "unrealized_pnl_unavailable"
    assert snapshot()["cash"] == "900"
    assert snapshot()["fill_count"] == 1
    assert reconcile()["passed"] is True


def test_kill_switch_still_precedes_the_unrealized_check(db):
    _init()
    assert _order("eth", symbol="ETH")["status"] == "filled"
    set_kill_switch(True)
    blocked = _order("btc")
    assert blocked["reason"] == "kill_switch_active"
    assert blocked["limits_evaluated"] is False
    assert snapshot()["fill_count"] == 1


def test_working_fill_rechecks_a_later_mark_down(db):
    _init(max_daily_loss=10)
    assert _order("held", symbol="ETH")["status"] == "filled"
    mark_to_market({"ETH": "100"}, observed_at=NOW, now=NOW)
    assert _order("pending", working=True)["status"] == "accepted"
    mark_to_market({"ETH": "50"}, observed_at=NOW, now=NOW)
    with pytest.raises(ValueError, match="daily_loss_limit_reached"):
        apply_paper_fill(
            client_order_id="pending", client_fill_id="fill-1", quantity="1",
            observed_at=NOW, now=NOW,
        )
    state = recover()["snapshot"]
    assert state["cash"] == "900"
    assert state["fill_count"] == 1
    assert state["open_orders"][0]["client_order_id"] == "pending"
    assert state["open_orders"][0]["leaves_quantity"] == "1"
    assert reconcile()["passed"] is True


def test_same_price_add_without_a_stored_mark_still_fills(db):
    _init()
    assert _order("first")["status"] == "filled"
    assert _order("second")["status"] == "filled"
    assert snapshot()["positions"] == [{"symbol": "BTC", "quantity": "2", "average_cost": "100"}]
    assert snapshot()["live_execution"] is False
    assert reconcile()["passed"] is True


def test_live_execution_stays_refused_when_the_loss_gate_would_block(db):
    _init()
    assert _order("open", quantity="2")["status"] == "filled"
    mark_to_market({"BTC": "90"}, observed_at=NOW, now=NOW)
    with pytest.raises(ValueError, match="live_execution_disabled"):
        _order("live", live_execution=True)
    assert snapshot()["fill_count"] == 1
    assert snapshot()["broker_order_submitted"] is False


def test_http_mark_down_rejection_stays_paper_only(db):
    client = TestClient(app)
    opened = client.post("/paper/execution/initialize", json={
        "initial_cash": "1000",
        "limits": {"max_daily_loss": 20, "max_order_notional": 10000,
                   "max_position_notional": 10000, "max_gross_exposure": 10000},
    })
    assert opened.status_code == 200
    first = client.post("/paper/execution/orders", json={
        "client_order_id": "open", "symbol": "BTC", "side": "BUY",
        "quantity": "2", "price": "100", "observed_at": NOW, "now": NOW,
    })
    assert first.status_code == 200
    assert first.json()["status"] == "filled"
    marked = client.post("/paper/execution/marks", json={
        "marks": {"BTC": "90"}, "observed_at": NOW, "now": NOW,
    })
    assert marked.status_code == 200
    blocked = client.post("/paper/execution/orders", json={
        "client_order_id": "add", "symbol": "BTC", "side": "BUY",
        "quantity": "1", "price": "100", "observed_at": NOW, "now": NOW,
    })
    assert blocked.status_code == 200
    body = blocked.json()
    assert body["reason"] == "daily_loss_limit_reached"
    assert body["fill"] is None
    assert body["live_execution"] is False
    assert body["broker_order_submitted"] is False
    report = client.get("/paper/execution/reconciliation-report")
    assert report.status_code == 200
    assert report.json()["passed"] is True
    assert report.json()["daily_loss_basis"] == "realized_plus_adverse_unrealized"
    assert report.json()["live_execution"] is False
