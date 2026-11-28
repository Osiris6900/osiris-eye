"""Realized paper P&L follows the strategy tag stored on the order."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app
from osiris_api.paper_execution import (
    LIVE_EXECUTION_ENABLED,
    initialize_paper_account,
    snapshot,
    submit_paper_order,
)
from osiris_api.paper_strategy_pnl import paper_strategy_pnl
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


def _order(key, side, price, strategy_id, symbol="BTC", quantity="1", fee="0"):
    assert submit_paper_order(
        client_order_id=key, symbol=symbol, side=side, quantity=quantity, price=price,
        fee=fee, observed_at=FRESH, now=NOW, risk_service_available=True,
        kill_switch_active=False, strategy_id=strategy_id,
    )["status"] == "filled"


def test_live_execution_stays_disabled():
    assert LIVE_EXECUTION_ENABLED is False


def test_two_tags_sum_to_the_account_and_the_closing_order_keeps_the_residual(book):
    _order("buy-a", "BUY", "100", "alpha")
    _order("sell-b", "SELL", "110", "beta", fee="1")
    _order("buy-eth", "BUY", "50", None, symbol="ETH")
    before = snapshot()
    report = paper_strategy_pnl()
    assert report["state"] == "ready"
    assert report["unrealized_pnl_attributed"] is False
    assert report["realized_pnl_follows_closing_order"] is True
    assert report["strategy_budgets_enforced"] is False
    assert report["book_mutated"] is False
    assert report["live_execution"] is False
    assert report["strategies"] == [
        {"strategy_id": "alpha", "realized_pnl": "0", "fees": "0", "fills": 1},
        {"strategy_id": "beta", "realized_pnl": "9", "fees": "1", "fills": 1},
        {"strategy_id": None, "realized_pnl": "0", "fees": "0", "fills": 1},
    ]
    assert report["realized_pnl"] == "9"
    assert report["fees"] == "1"
    assert report["realized_pnl"] == before["realized_pnl"]
    assert snapshot() == before


def test_an_empty_book_is_ready(book):
    report = paper_strategy_pnl()
    assert report["state"] == "ready"
    assert report["strategies"] == []
    assert report["realized_pnl"] == "0"
    assert report["fees"] == "0"


def test_corrupt_book_is_unavailable(book):
    _order("buy-a", "BUY", "100", "alpha")
    with sqlite3.connect(book) as conn:
        conn.execute("UPDATE paper_exec_audit SET payload='{}' WHERE seq=1")
    report = paper_strategy_pnl()
    assert report["state"] == "unavailable"
    assert report["reason"] == "audit_chain_corrupt"
    assert report["strategies"] == []
    assert report["realized_pnl"] is None


def test_http_ignores_caller_strategies(book):
    _order("buy-a", "BUY", "100", "alpha")
    _order("sell-a", "SELL", "110", "alpha")
    response = TestClient(app).get("/paper/execution/strategy-pnl", params={
        "strategy_id": "caller", "realized_pnl": "999999",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["realized_pnl"] == "10"
    assert body["strategies"] == [
        {"strategy_id": "alpha", "realized_pnl": "10", "fees": "0", "fills": 2},
    ]
    assert "caller" not in response.text
