"""Paper route records are derived from the book, not from a caller price."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app
from osiris_api.paper_execution import (
    LIVE_EXECUTION_ENABLED,
    apply_paper_fill,
    initialize_paper_account,
    submit_paper_order,
)
from osiris_api.paper_routes import paper_route_records
from osiris_api.risk import RiskLimits

NOW = "2026-11-23T15:00:00+00:00"
FRESH = "2026-11-23T14:59:30+00:00"
LIMITS = RiskLimits(
    max_order_notional=100_000, max_position_notional=100_000,
    max_daily_loss=100_000, max_gross_exposure=100_000,
)


@pytest.fixture
def book(tmp_path, monkeypatch):
    path = tmp_path / "paper.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(path))
    monkeypatch.setenv("OSIRIS_INSTRUMENT_DB_PATH", str(tmp_path / "instruments.sqlite3"))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    monkeypatch.delenv("OSIRIS_INSTRUMENT_FIXTURE", raising=False)
    initialize_paper_account("100000", LIMITS)
    return path


def _order(key, *, working=False, price="100"):
    return submit_paper_order(
        client_order_id=key, symbol="BTC", side="BUY", quantity="1", price=price,
        fee="0", observed_at=FRESH, now=NOW, risk_service_available=True,
        kill_switch_active=False, working=working,
    )


def test_live_routing_stays_disabled():
    assert LIVE_EXECUTION_ENABLED is False


def test_fill_at_the_limit_has_zero_shortfall(book):
    assert _order("at-limit")["status"] == "filled"
    report = paper_route_records()
    assert report["state"] == "ready"
    assert report["live_routing_enabled"] is False
    assert report["live_execution"] is False
    assert report["route"] == "paper_book"
    route = report["routes"][0]
    assert route["client_order_id"] == "at-limit"
    assert route["average_fill_price"] == "100"
    assert route["shortfall_bps"] == "0"
    assert route["venue"] == "PAPER"
    assert route["venue_status"] == "master"
    assert route["live_routing_enabled"] is False


def test_better_fill_is_negative_shortfall_for_a_buy(book):
    assert _order("resting", working=True, price="100")["state"] == "accepted"
    apply_paper_fill(
        client_order_id="resting", client_fill_id="better", quantity="1",
        price="90", observed_at=FRESH, now=NOW,
    )
    route = paper_route_records()["routes"][0]
    assert route["average_fill_price"] == "90"
    assert route["shortfall_bps"] == "-1000"
    assert route["state"] == "filled"


def test_unfilled_route_has_no_shortfall(book):
    assert _order("open", working=True)["state"] == "accepted"
    route = paper_route_records()["routes"][0]
    assert route["average_fill_price"] is None
    assert route["shortfall_bps"] is None
    assert route["filled_quantity"] == "0"


def test_corrupt_book_does_not_invent_a_route(book):
    _order("at-limit")
    with sqlite3.connect(book) as conn:
        conn.execute("UPDATE paper_exec_audit SET payload='{}' WHERE seq=1")
    report = paper_route_records()
    assert report["state"] == "unavailable"
    assert report["reason"] == "audit_chain_corrupt"
    assert report["routes"] == []
    assert report["live_routing_enabled"] is False


def test_http_record_ignores_a_caller_price(book):
    _order("at-limit")
    response = TestClient(app).get("/paper/execution/routes", params={"shortfall_bps": "999"})
    assert response.status_code == 200
    body = response.json()
    assert body["routes"][0]["shortfall_bps"] == "0"
    assert body["live_routing_enabled"] is False
    assert TestClient(app).post("/paper/execution/routes", json={"limit_price": "1"}).status_code == 405
