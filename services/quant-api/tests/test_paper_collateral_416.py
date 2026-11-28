"""Paper collateral is the book's cash, not a caller balance."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app
from osiris_api.paper_collateral import paper_collateral
from osiris_api.paper_execution import (
    LIVE_EXECUTION_ENABLED,
    initialize_paper_account,
    mark_to_market,
    snapshot,
    submit_paper_order,
)
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


def test_live_execution_stays_disabled():
    assert LIVE_EXECUTION_ENABLED is False


def test_filled_buy_reduces_free_cash_and_a_mark_values_the_long(book):
    before = snapshot()
    assert submit_paper_order(
        client_order_id="buy", symbol="BTC", side="BUY", quantity="1", price="100",
        fee="0", observed_at=FRESH, now=NOW, risk_service_available=True,
        kill_switch_active=False,
    )["status"] == "filled"
    unmarked = paper_collateral(NOW)
    assert unmarked["state"] == "incomplete"
    assert unmarked["reason"] == "mark_required"
    assert unmarked["cash"] == "99900"
    assert unmarked["reserved_cash"] == "0"
    assert unmarked["free_cash"] == "99900"
    assert unmarked["margin_loan"] == "0"
    assert unmarked["long_market_value"] is None
    assert unmarked["book_mutated"] is False
    mark_to_market({"BTC": "110"}, observed_at=FRESH, now=NOW)
    marked = paper_collateral(NOW)
    assert marked["state"] == "ready"
    assert marked["long_market_value"] == "110"
    assert marked["live_execution"] is False
    assert snapshot()["cash"] == "99900"
    assert snapshot()["positions"] != before["positions"]


def test_open_buy_reserves_cash(book):
    assert submit_paper_order(
        client_order_id="resting", symbol="BTC", side="BUY", quantity="2", price="100",
        fee="0", observed_at=FRESH, now=NOW, risk_service_available=True,
        kill_switch_active=False, working=True,
    )["state"] == "accepted"
    report = paper_collateral(NOW)
    assert report["cash"] == "100000"
    assert report["reserved_cash"] == "200"
    assert report["free_cash"] == "99800"
    assert report["margin_loan"] == "0"
    assert snapshot()["cash"] == "100000"


def test_corrupt_book_does_not_report_free_cash(book):
    with sqlite3.connect(book) as conn:
        conn.execute("UPDATE paper_exec_audit SET payload='{}' WHERE seq=1")
    report = paper_collateral(NOW)
    assert report["state"] == "unavailable"
    assert report["reason"] == "audit_chain_corrupt"
    assert report["cash"] is None
    assert report["margin_loan"] == "0"


def test_http_ignores_a_caller_balance(book):
    response = TestClient(app).get("/paper/execution/collateral", params={
        "as_of": NOW, "cash": "1", "positions": "100",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["cash"] == "100000"
    assert body["free_cash"] == "100000"
    assert body["margin_model"] == "cash_fully_funded"
    assert body["book_mutated"] is False
    assert snapshot()["cash"] == "100000"
