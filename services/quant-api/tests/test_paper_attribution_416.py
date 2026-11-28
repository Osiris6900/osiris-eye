"""Paper book attribution reconciles symbol P&L to the canonical account."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app
from osiris_api.paper_attribution import paper_book_attribution
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


def _order(key, symbol, side, price, quantity="1", fee="0"):
    assert submit_paper_order(
        client_order_id=key, symbol=symbol, side=side, quantity=quantity, price=price,
        fee=fee, observed_at=FRESH, now=NOW, risk_service_available=True,
        kill_switch_active=False,
    )["status"] == "filled"


def test_live_execution_stays_disabled():
    assert LIVE_EXECUTION_ENABLED is False


def test_symbol_rows_reconcile_fees_and_price_movement(book):
    _order("btc-buy", "BTC", "BUY", "100", quantity="2")
    mark_to_market({"BTC": "110"}, observed_at=FRESH, now=NOW)
    _order("btc-sell", "BTC", "SELL", "110", quantity="1", fee="1")
    _order("eth-buy", "ETH", "BUY", "50", quantity="2")
    mark_to_market({"BTC": "110", "ETH": "40"}, observed_at=FRESH, now=NOW)
    before = snapshot()
    report = paper_book_attribution(NOW)
    assert report["state"] == "ready"
    assert report["reconciled_to_account"] is True
    assert report["strategy_attributed"] is False
    assert report["slippage_attributed"] is False
    assert report["book_mutated"] is False
    assert report["live_execution"] is False
    assert report["source"] == "paper_book"
    assert report["symbols"] == [
        {
            "symbol": "BTC",
            "realized_pnl": "9",
            "fees": "1",
            "unrealized_pnl": "10",
            "price_movement": "20",
        },
        {
            "symbol": "ETH",
            "realized_pnl": "0",
            "fees": "0",
            "unrealized_pnl": "-20",
            "price_movement": "-20",
        },
    ]
    assert report["realized_pnl"] == "9"
    assert report["fees"] == "1"
    assert report["unrealized_pnl"] == "-10"
    assert report["price_movement"] == "0"
    assert report["realized_pnl"] == before["realized_pnl"]
    assert report["unrealized_pnl"] == before["unrealized_pnl"]
    assert snapshot() == before


def test_a_closed_book_does_not_need_a_mark(book):
    _order("buy", "BTC", "BUY", "100")
    _order("sell", "BTC", "SELL", "110")
    report = paper_book_attribution(NOW)
    assert report["state"] == "ready"
    assert report["symbols"] == [{
        "symbol": "BTC",
        "realized_pnl": "10",
        "fees": "0",
        "unrealized_pnl": "0",
        "price_movement": "10",
    }]
    assert report["unrealized_pnl"] == "0"


def test_missing_mark_does_not_publish_a_partial_total(book):
    _order("btc", "BTC", "BUY", "100")
    report = paper_book_attribution(NOW)
    assert report["state"] == "incomplete"
    assert report["reason"] == "mark_required"
    assert report["symbols"] == []
    assert report["price_movement"] is None
    assert report["realized_pnl"] is None
    assert report["reconciled_to_account"] is False


def test_stale_mark_is_not_a_price(book):
    _order("btc", "BTC", "BUY", "100")
    mark_to_market({"BTC": "110"}, observed_at=FRESH, now=NOW)
    report = paper_book_attribution("2026-11-23T16:00:00+00:00")
    assert report["state"] == "incomplete"
    assert report["reason"] == "mark_required"
    assert report["price_movement"] is None


def test_corrupt_book_is_unavailable(book):
    _order("btc", "BTC", "BUY", "100")
    with sqlite3.connect(book) as conn:
        conn.execute("UPDATE paper_exec_audit SET payload='{}' WHERE seq=1")
    report = paper_book_attribution(NOW)
    assert report["state"] == "unavailable"
    assert report["reason"] == "audit_chain_corrupt"
    assert report["price_movement"] is None
    assert report["symbols"] == []


def test_http_ignores_caller_pnl(book):
    _order("buy", "BTC", "BUY", "100")
    _order("sell", "BTC", "SELL", "110")
    response = TestClient(app).get("/paper/execution/attribution", params={
        "as_of": NOW, "strategy": "alpha", "slippage": "0", "pnl": "999999",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["realized_pnl"] == "10"
    assert body["price_movement"] == "10"
    assert body["strategy_attributed"] is False
    assert body["slippage_attributed"] is False
    assert "alpha" not in response.text
