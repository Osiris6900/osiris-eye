"""Package 41: the paper book must refuse an instrument the master does not allow.

On main, before this change, these cases were accepted: an unknown symbol filled,
a price off the tick filled, and a quantity that was not a lot multiple filled.
The pre-trade gate also approved a context that named no symbol.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from osiris_api.control_plane.broker import AlpacaPaperBroker
from osiris_api.control_plane.database import Database
from osiris_api.instrument_master import InstrumentMasterStore, instrument_db_path
from osiris_api.main import app
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
from osiris_api.paper_workflow import evaluate_paper_workflow
from osiris_api.risk import RiskLimits, check_execution

NOW = "2026-11-23T15:00:00+00:00"  # Monday, 10:00 America/New_York
FRESH = "2026-11-23T14:59:30+00:00"
CLIENT = TestClient(app)


def _init(path):
    initialize_paper_account("100000", RiskLimits(
        max_order_notional=100_000, max_position_notional=100_000,
        max_daily_loss=100_000, max_gross_exposure=100_000,
    ))


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "paper.sqlite3"))
    monkeypatch.setenv("OSIRIS_INSTRUMENT_DB_PATH", str(tmp_path / "instruments.sqlite3"))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    monkeypatch.delenv("OSIRIS_INSTRUMENT_FIXTURE", raising=False)
    _init(tmp_path)
    return tmp_path


def _order(symbol, *, price="100", quantity="1", key="order-1", now=NOW, observed_at=FRESH):
    return submit_paper_order(
        client_order_id=key, symbol=symbol, side="BUY", quantity=quantity, price=price,
        fee="0", observed_at=observed_at, now=now, risk_service_available=True,
        kill_switch_active=False,
    )


def _decided(symbol=None):
    rows = []
    for row in snapshot()["audit"]:
        if row["event"] != "order_decided":
            continue
        payload = json.loads(row["payload"])
        if symbol is None or payload.get("symbol") == symbol:
            rows.append(payload)
    return rows


def test_live_execution_stays_disabled():
    assert LIVE_EXECUTION_ENABLED is False


def test_unknown_symbol_is_rejected(book):
    result = _order("NOT-A-REAL-SYMBOL")
    assert result["status"] == "instrument_rejected"
    assert result["state"] == "rejected"
    assert result["reason"] == "unknown_instrument"
    assert result["fill"] is None
    assert result["live_execution"] is False
    assert result["broker_order_submitted"] is False
    assert snapshot()["positions"] == []
    assert snapshot()["cash"] == "100000"
    payload = _decided("NOT-A-REAL-SYMBOL")[-1]
    assert payload["reason"] == "unknown_instrument"
    assert payload["instrument_master_version"]
    assert reconcile()["passed"] is True


def test_price_off_tick_and_odd_lot_are_rejected(book):
    off_tick = _order("RELIANCE", price="100.03", key="off-tick")
    assert off_tick["status"] == "instrument_rejected"
    assert off_tick["reason"] == "invalid_tick_size"
    odd_lot = _order("RELIANCE", price="100.05", quantity="1.5", key="odd-lot")
    assert odd_lot["reason"] == "invalid_lot_size"
    assert snapshot()["positions"] == []
    assert reconcile()["passed"] is True


def test_inactive_and_not_yet_effective_instruments_are_rejected(book):
    halted = _order("HALT", price="10.00", key="halted")
    assert halted["reason"] == "instrument_inactive"
    delisted = _order("GONE", price="10.00", key="delisted")
    assert delisted["reason"] == "instrument_inactive"
    future = _order("SOON", price="10.00", key="future")
    assert future["reason"] == "instrument_not_yet_effective"
    expired = _order("OLD", price="10.00", key="expired")
    assert expired["reason"] == "instrument_inactive"
    assert snapshot()["cash"] == "100000"
    assert reconcile()["passed"] is True


def test_accepted_order_fill_and_mark_record_the_master_version(book):
    # Monday 04:30Z is 10:00 Asia/Kolkata, inside the stored 09:15–15:30 session.
    nse_now, nse_fresh = "2026-11-23T04:30:00+00:00", "2026-11-23T04:29:30+00:00"
    opened = _order(
        "RELIANCE", price="100.05", quantity="2", key="good", now=nse_now, observed_at=nse_fresh,
    )
    assert opened["status"] == "filled"
    assert opened["state"] == "filled"
    version = opened["instrument_master_version"]
    assert version == InstrumentMasterStore(instrument_db_path()).catalog_version()
    assert len(version) == 64
    payload = _decided("RELIANCE")[-1]
    assert payload["instrument_master_version"] == version
    assert payload["instrument_id"] == "equity:NSE:RELIANCE"
    assert payload["instrument_version"]
    fills = [
        json.loads(row["payload"]) for row in snapshot()["audit"] if row["event"] == "fill_recorded"
    ]
    assert fills[-1]["instrument_master_version"] == version
    marked = mark_to_market({"RELIANCE": "100.10"}, observed_at=nse_fresh, now=nse_now)
    assert marked["live_execution"] is False
    marks = [
        json.loads(row["payload"]) for row in snapshot()["audit"] if row["event"] == "mark_to_market"
    ]
    assert marks[-1]["instrument_master_version"] == version
    assert snapshot()["marks"] == [{
        "symbol": "RELIANCE", "price": "100.1", "observed_at": nse_fresh,
    }]
    assert reconcile()["passed"] is True


def test_fill_off_tick_does_not_print(book):
    opened = submit_paper_order(
        client_order_id="resting", symbol="AAPL", side="BUY", quantity="4", price="100.00",
        fee="0", observed_at=FRESH, now=NOW, working=True,
    )
    assert opened["state"] == "accepted"
    with pytest.raises(ValueError, match="invalid_tick_size"):
        apply_paper_fill(
            client_order_id="resting", client_fill_id="bad-tick", quantity="1",
            price="100.001", observed_at=FRESH, now=NOW,
        )
    assert snapshot()["positions"] == []
    assert snapshot()["fill_count"] == 0
    assert reconcile()["passed"] is True


def test_mark_rejects_unknown_symbol_and_off_tick_price(book):
    _order("AAPL", price="100.00", key="seed-position")
    with pytest.raises(ValueError, match="unknown_instrument"):
        mark_to_market({"NOT-A-REAL-SYMBOL": "10"}, observed_at=FRESH, now=NOW)
    with pytest.raises(ValueError, match="invalid_tick_size"):
        mark_to_market({"AAPL": "100.001"}, observed_at=FRESH, now=NOW)
    assert snapshot()["marks"] == []
    assert reconcile()["passed"] is True


def test_pretrade_gate_fails_closed_without_a_tradable_instrument():
    # Monday 2026-11-23T15:00Z is inside the equity clock window, so a halt stays a halt.
    decision_time_ms = 1_795_446_000_000
    context = PaperOrderContext(
        quantity=2, price=100, current_position_notional=0, daily_pnl=0,
        observation_time_ms=decision_time_ms - 200, decision_time_ms=decision_time_ms,
        model_approved=True, feed_authenticated=True, feed_entitled=True,
        kill_switch_active=False,
    )
    assert authorize_paper_order(context) == (False, "unknown_instrument")
    halted = replace(context, symbol="HALT", quantity=1, price=10)
    assert authorize_paper_order(halted) == (False, "instrument_inactive")
    future = replace(context, symbol="SOON", quantity=1, price=10)
    assert authorize_paper_order(future) == (False, "instrument_not_yet_effective")
    off_tick = replace(context, symbol="RELIANCE", quantity=1, price=100.03)
    assert authorize_paper_order(off_tick) == (False, "invalid_tick_size")
    odd_lot = replace(context, symbol="AAPL", quantity=1.5, price=100)
    assert authorize_paper_order(odd_lot) == (False, "invalid_lot_size")
    allowed = replace(context, symbol="BTC", quantity=1, price=100)
    assert authorize_paper_order(allowed) == (True, "approved_for_simulation_only")
    # A halt still wins over a missing symbol.
    assert authorize_paper_order(replace(context, kill_switch_active=True)) == (
        False, "kill_switch_active",
    )


def test_check_execution_and_workflow_reject_unknown_symbols():
    approved, reason, evaluated = check_execution(
        side="BUY", quantity=1, price=100, current_position_notional=0,
        current_gross_exposure=0, daily_pnl=0, kill_switch_active=False,
        market_data_fresh=True, risk_service_available=True, symbol="NOT-A-REAL-SYMBOL",
    )
    assert (approved, reason, evaluated) == (False, "unknown_instrument", False)
    workflow = evaluate_paper_workflow(
        symbol="NOT-A-REAL-SYMBOL", signals=[0.0, 1.0], asset_returns=[0.0, 0.01],
        quantity=1, price=100, kill_switch_active=False, market_data_fresh=True,
        risk_service_available=True, current_gross_exposure=0.0,
    )
    assert workflow["risk"]["approved_for_paper"] is False
    assert workflow["risk"]["reason"] == "unknown_instrument"
    assert workflow["paper_order_intent"]["status"] == "BLOCKED_BY_RISK"
    assert workflow["execution"]["broker_order_submitted"] is False
    assert workflow["execution"]["live_execution"] is False


def test_catalog_get_is_read_only_and_versions_match_across_databases(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_INSTRUMENT_DB_PATH", str(tmp_path / "instruments.sqlite3"))
    monkeypatch.delenv("OSIRIS_INSTRUMENT_FIXTURE", raising=False)
    first = InstrumentMasterStore(str(tmp_path / "one.sqlite3"))
    second = InstrumentMasterStore(str(tmp_path / "two.sqlite3"))
    assert first.ensure_seeded() == second.ensure_seeded()
    response = CLIENT.get("/market-data/instruments/catalog")
    assert response.status_code == 200
    body = response.json()
    assert body["catalog_version"] == first.catalog_version()
    assert body["live_execution"] is False
    assert body["live_trading_authorized"] is False
    assert body["exchange_connectivity_verified"] is False
    assert body["corporate_actions_applied_to_positions"] is False
    assert body["trading_hours_enforced"] is True
    assert body["exchange_weekends_enforced"] is True
    assert body["exchange_holidays_enforced"] is True
    calendar = body["exchange_holiday_calendar"]
    assert calendar["bse_seeded"] is False
    assert calendar["early_closes_seeded"] is True
    assert {row["venue"]: row["early_close_count"] for row in calendar["venues"]} == {
        "NSE": 0, "XNYS": 8,
    }
    symbols = {item["instrument"]["symbol"] for item in body["instruments"]}
    assert {"AAPL", "RELIANCE", "BTC", "BTC-USD", "ETH", "ETH-USD", "TEST", "HALT", "SOON"} <= symbols
    actions = {item["action_type"] for item in body["corporate_actions"]}
    assert {"split", "cash_dividend"} <= actions
    assert all(item["applied_to_positions"] is False for item in body["corporate_actions"])
    write = CLIENT.post("/market-data/instruments/catalog", json={"instrument": {}})
    assert write.status_code == 405


def test_broker_submit_rejects_unknown_symbol_before_transport(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_INSTRUMENT_DB_PATH", str(tmp_path / "instruments.sqlite3"))
    monkeypatch.delenv("OSIRIS_INSTRUMENT_FIXTURE", raising=False)

    class Transport:
        def __init__(self):
            self.calls = []

        def request(self, method, url, **kwargs):
            self.calls.append((method, url))
            raise AssertionError("transport_called")

    transport = Transport()
    service = AlpacaPaperBroker(
        Database(str(tmp_path / "broker.db")), {"key": "FIXTURE", "secret": "FIXTURE"},
        enabled=True, transport=transport,
    )
    order = {
        "client_order_id": "order-1", "symbol": "NOPE", "side": "BUY",
        "quantity": "1", "limit_price": "100",
    }
    with pytest.raises(ValueError, match="unknown_instrument"):
        service.submit(
            order, limits=RiskLimits(), position=0, gross=0, daily_pnl=0,
            kill_switch=False, fresh=True,
        )
    assert transport.calls == []
