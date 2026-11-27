"""Day time-in-force for canonical paper orders.

A resting order is fillable only until its session ends. These tests use an
injected clock, and the exchange calendar when a venue is bound. No live order
is routed.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from osiris_api.exchange_calendar import ExchangeCalendarStore
from osiris_api.main import app
from osiris_api.paper_execution import (
    apply_paper_fill,
    cancel_paper_order,
    expire_paper_orders,
    initialize_paper_account,
    reconciliation_report,
    recover,
    snapshot,
    submit_paper_order,
)
from osiris_api.risk import RiskLimits


NOW = "2026-11-08T16:00:00+00:00"
FRESH = "2026-11-08T15:59:30+00:00"
SAME_DAY_EDGE = "2026-11-08T23:59:59+00:00"
NEXT = "2026-11-09T16:00:00+00:00"
NEXT_FRESH = "2026-11-09T15:59:30+00:00"
UTC_EXPIRY = "2026-11-09T00:00:00+00:00"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "paper-day.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(path))
    monkeypatch.setenv("OSIRIS_CALENDAR_DB_PATH", str(tmp_path / "calendar.sqlite3"))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    return path


def _init():
    initialize_paper_account("1000", RiskLimits(
        max_order_notional=10_000, max_position_notional=10_000,
        max_daily_loss=1_000, max_gross_exposure=10_000,
    ))


def _working(client_order_id="buy-1", **overrides):
    payload = dict(
        client_order_id=client_order_id, symbol="BTC", side="BUY",
        quantity="10", price="100", fee="0", observed_at=FRESH, now=NOW, working=True,
    )
    payload.update(overrides)
    return submit_paper_order(**payload)


def _fill(client_order_id="buy-1", client_fill_id="fill-1", quantity="4", **overrides):
    payload = dict(
        client_order_id=client_order_id, client_fill_id=client_fill_id, quantity=quantity,
        observed_at=FRESH, now=NOW,
    )
    payload.update(overrides)
    return apply_paper_fill(**payload)


def _events():
    return [row["event"] for row in snapshot()["audit"]]


def test_working_order_defaults_to_day_and_replays(db):
    _init()
    opened = _working()
    assert opened["time_in_force"] == "day"
    assert opened["expires_at"] == UTC_EXPIRY
    assert opened["venue"] == ""
    assert opened["state"] == "accepted"
    replay = _working(time_in_force="DAY")
    assert replay["status"] == "idempotent_replay"
    assert replay["time_in_force"] == "day"
    assert snapshot()["orders"][0]["time_in_force"] == "day"
    assert snapshot()["open_orders"][0]["expires_at"] == UTC_EXPIRY


@pytest.mark.parametrize("value", ["gtc", "ioc", "opg", ""])
def test_only_day_time_in_force_is_accepted(db, value):
    _init()
    with pytest.raises(ValueError, match="invalid_time_in_force"):
        _working(time_in_force=value)
    assert snapshot()["order_count"] == 0


def test_fresh_next_day_fill_expires_and_releases_reserved_cash(db):
    _init()
    _working()
    assert _fill()["state"] == "partially_filled"
    edge = _fill(client_fill_id="same-day", quantity="1", observed_at=SAME_DAY_EDGE, now=SAME_DAY_EDGE)
    assert edge["state"] == "partially_filled"
    assert edge["filled_quantity"] == "5"
    with pytest.raises(ValueError, match="order_expired"):
        _fill(client_fill_id="next-day", quantity="1", observed_at=NEXT_FRESH, now=NEXT)
    view = snapshot()
    expired = view["orders"][0]
    assert expired["state"] == "expired"
    assert expired["filled_quantity"] == "5"
    assert expired["leaves_quantity"] == "0"
    assert expired["time_in_force"] == "day"
    assert view["cash"] == "500"
    assert view["positions"][0]["quantity"] == "5"
    assert view["open_orders"] == []
    assert view["fill_count"] == 2
    assert _events().count("order_expired") == 1
    assert _events().count("fill_rejected") == 1
    replay = _fill()
    assert replay["status"] == "idempotent_replay"
    released = submit_paper_order(
        client_order_id="buy-2", symbol="BTC", side="BUY", quantity="5", price="100",
        fee="0", observed_at=NEXT_FRESH, now=NEXT,
    )
    assert released["state"] == "filled"
    assert snapshot()["cash"] == "0"
    again = expire_paper_orders(now=NEXT)
    assert again["expired"] == []
    assert again["live_execution"] is False
    assert again["broker_order_submitted"] is False
    assert _events().count("order_expired") == 1
    assert reconciliation_report()["passed"] is True


def test_partial_sell_expiry_releases_reserved_shares(db):
    _init()
    opened = submit_paper_order(
        client_order_id="open", symbol="BTC", side="BUY", quantity="10", price="100",
        fee="0", observed_at=FRESH, now=NOW,
    )
    assert opened["state"] == "filled"
    resting = submit_paper_order(
        client_order_id="exit", symbol="BTC", side="SELL", quantity="10", price="100",
        fee="0", observed_at=FRESH, now=NOW, working=True,
    )
    assert resting["time_in_force"] == "day"
    assert apply_paper_fill(
        client_order_id="exit", client_fill_id="sold", quantity="4",
        observed_at=FRESH, now=NOW,
    )["state"] == "partially_filled"
    blocked = submit_paper_order(
        client_order_id="exit-2", symbol="BTC", side="SELL", quantity="6", price="100",
        fee="0", observed_at=FRESH, now=NOW,
    )
    assert blocked["reason"] == "insufficient_paper_position"
    assert expire_paper_orders(now=NEXT)["expired"] == ["exit"]
    view = snapshot()
    expired = next(row for row in view["orders"] if row["client_order_id"] == "exit")
    assert expired["state"] == "expired"
    assert expired["filled_quantity"] == "4"
    assert expired["leaves_quantity"] == "0"
    assert view["positions"][0]["quantity"] == "6"
    assert view["cash"] == "400"
    freed = submit_paper_order(
        client_order_id="exit-3", symbol="BTC", side="SELL", quantity="6", price="100",
        fee="0", observed_at=NEXT_FRESH, now=NEXT,
    )
    assert freed["state"] == "filled"
    assert snapshot()["positions"] == []
    assert snapshot()["cash"] == "1000"
    assert reconciliation_report()["passed"] is True


def test_cancel_after_expiry_is_rejected_and_replay_keeps_the_expired_order(db):
    _init()
    _working()
    _fill()
    with pytest.raises(ValueError, match="order_expired"):
        cancel_paper_order(client_order_id="buy-1", now=NEXT)
    assert snapshot()["orders"][0]["state"] == "expired"
    assert snapshot()["orders"][0]["filled_quantity"] == "4"
    assert snapshot()["cash"] == "600"
    assert _events().count("order_cancelled") == 0
    assert _events().count("order_expired") == 1
    with pytest.raises(ValueError, match="order_expired"):
        cancel_paper_order(client_order_id="buy-1")
    with pytest.raises(ValueError, match="order_expired"):
        _fill(client_fill_id="after", quantity="1", observed_at=NEXT_FRESH, now=NEXT)
    resumed = recover()
    assert resumed["open_orders"] == []
    stored = next(row for row in resumed["snapshot"]["orders"] if row["client_order_id"] == "buy-1")
    assert stored["state"] == "expired"
    assert stored["filled_quantity"] == "4"
    assert stored["leaves_quantity"] == "0"
    assert stored["expires_at"] == UTC_EXPIRY
    assert reconciliation_report()["passed"] is True
    with sqlite3.connect(db) as conn:
        conn.execute(
            """UPDATE paper_exec_orders
               SET state='accepted', status='accepted', leaves_quantity='6'
               WHERE client_order_id='buy-1'"""
        )
    assert "journal_order:buy-1" in reconciliation_report()["breaks"]


def test_expiry_sweep_is_idempotent_and_a_crash_rolls_back(db):
    _init()
    _working(quantity="1")
    _working(client_order_id="later", quantity="1", now=NEXT, observed_at=NEXT_FRESH)
    with pytest.raises(RuntimeError, match="simulated_crash_before_commit"):
        expire_paper_orders(now=UTC_EXPIRY, simulate_crash=True)
    assert {row["client_order_id"]: row["state"] for row in snapshot()["open_orders"]} == {
        "buy-1": "accepted", "later": "accepted",
    }
    assert _events().count("order_expired") == 0
    first = expire_paper_orders(now=UTC_EXPIRY)
    assert first["expired"] == ["buy-1"]
    assert first["as_of"] == UTC_EXPIRY
    second = expire_paper_orders(now=UTC_EXPIRY)
    assert second["expired"] == []
    assert _events().count("order_expired") == 1
    assert snapshot()["open_orders"][0]["client_order_id"] == "later"
    with pytest.raises(ValueError, match="decision_clock_required"):
        expire_paper_orders(now=None)
    assert reconciliation_report()["passed"] is True


def test_calendar_session_close_binds_the_day_order(db):
    store = ExchangeCalendarStore()
    store.put({
        "venue": "XNYS", "timezone": "America/New_York", "calendar_date": "2026-11-09",
        "sessions": [{"open": "09:30", "close": "16:00"}], "source": "test-fixture",
    }, "2026-01-01T00:00:00Z")
    opened_at = "2026-11-09T15:00:00+00:00"
    fresh = "2026-11-09T14:59:30+00:00"
    session = store.status("XNYS", opened_at, as_of=opened_at)
    assert session["is_open"] is True
    _init()
    opened = _working(now=opened_at, observed_at=fresh, venue=" xnys ")
    assert opened["venue"] == "XNYS"
    assert opened["time_in_force"] == "day"
    assert opened["expires_at"] == session["session_close"]
    assert opened["expires_at"] < "2026-11-10T00:00:00+00:00"
    before = datetime.fromisoformat(session["session_close"]) - timedelta(seconds=1)
    before_text = before.astimezone(timezone.utc).isoformat()
    assert _fill(quantity="4", observed_at=before_text, now=before_text)["state"] == "partially_filled"
    with pytest.raises(ValueError, match="order_expired"):
        _fill(
            client_fill_id="at-close", quantity="1",
            observed_at=session["session_close"], now=session["session_close"],
        )
    assert snapshot()["orders"][0]["state"] == "expired"
    assert snapshot()["orders"][0]["filled_quantity"] == "4"
    assert snapshot()["cash"] == "600"
    payload = json.loads(snapshot()["audit"][-2]["payload"])
    assert snapshot()["audit"][-2]["event"] == "order_expired"
    assert payload["released_quantity"] == "6"
    assert payload["filled_quantity"] == "4"
    assert payload["as_of"] == session["session_close"]
    assert expire_paper_orders(now=session["session_close"])["expired"] == []
    assert reconciliation_report()["passed"] is True


def test_resting_order_requires_an_open_session_when_a_venue_is_bound(db):
    store = ExchangeCalendarStore()
    store.put({
        "venue": "XNYS", "timezone": "America/New_York", "calendar_date": "2026-11-09",
        "sessions": [{"open": "09:30", "close": "16:00"}], "source": "test-fixture",
    }, "2026-01-01T00:00:00Z")
    store.put({
        "venue": "XNYS", "timezone": "America/New_York", "calendar_date": "2026-11-26",
        "is_holiday": True, "sessions": [], "source": "test-fixture",
    }, "2026-01-01T00:00:00Z")
    _init()
    closed = _working(now="2026-11-09T22:00:00+00:00", observed_at="2026-11-09T21:59:30+00:00", venue="XNYS")
    assert closed["state"] == "rejected"
    assert closed["reason"] == "session_not_open"
    holiday = _working(
        client_order_id="holiday", now="2026-11-26T15:00:00+00:00",
        observed_at="2026-11-26T14:59:30+00:00", venue="XNYS",
    )
    assert holiday["reason"] == "session_not_open"
    missing = _working(
        client_order_id="missing", now=NOW, observed_at=FRESH, venue="XNAS",
    )
    assert missing["state"] == "rejected"
    assert missing["reason"] == "calendar_data_unavailable"
    assert snapshot()["open_orders"] == []
    replay = _working(client_order_id="missing", now=NOW, observed_at=FRESH, venue="XNAS")
    assert replay["status"] == "idempotent_replay"
    with pytest.raises(ValueError, match="idempotency_key_conflict"):
        _working(client_order_id="missing", now=NOW, observed_at=FRESH, venue="XNYS")


def test_http_expire_stays_paper_only(db, monkeypatch):
    from osiris_api.control_plane.clock import TrustedClock
    from osiris_api.control_plane import clock

    current = [datetime.fromisoformat(NOW)]
    monkeypatch.setattr(clock, "_runtime", TrustedClock(lambda: current[0]))
    client = TestClient(app)
    assert client.post("/paper/execution/initialize", json={"initial_cash": "1000"}).status_code == 200
    resting = client.post("/paper/execution/orders", json={
        "client_order_id": "http-1", "symbol": "BTC", "side": "BUY",
        "quantity": "4", "price": "100", "observed_at": FRESH, "working": True,
    })
    assert resting.status_code == 200
    assert resting.json()["time_in_force"] == "day"
    assert resting.json()["broker_order_submitted"] is False
    refused = client.post("/paper/execution/orders/expire", json={"now": NEXT})
    assert refused.status_code == 422
    assert refused.json()["detail"] == "caller_clock_rejected"
    assert snapshot()["orders"][0]["state"] == "accepted"
    current[0] = datetime.fromisoformat(NEXT)
    expired = client.post("/paper/execution/orders/expire", json={})
    assert expired.status_code == 200
    body = expired.json()
    assert body["expired"] == ["http-1"]
    assert body["live_execution"] is False
    assert body["broker_order_submitted"] is False
    assert body["mode"] == "paper_only"
    again = client.post("/paper/execution/orders/expire", json={})
    assert again.json()["expired"] == []
    late = client.post("/paper/execution/orders/http-1/fills", json={
        "client_fill_id": "late", "quantity": "1", "observed_at": NEXT_FRESH,
    })
    assert late.status_code == 422
    assert late.json()["detail"] == "order_expired"
    cancelled = client.post("/paper/execution/orders/http-1/cancel", json={})
    assert cancelled.status_code == 422
    assert cancelled.json()["detail"] == "order_expired"
    report = client.get("/paper/execution/reconciliation-report")
    assert report.status_code == 200
    assert report.json()["passed"] is True
    assert report.json()["open_orders"] == []
    assert report.json()["live_execution"] is False
