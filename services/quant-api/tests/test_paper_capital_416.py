"""Paper capital usage is the book's equity and limits, not a caller budget."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app
from osiris_api.paper_capital import paper_capital_usage
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


@pytest.fixture
def book(tmp_path, monkeypatch):
    path = tmp_path / "paper.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(path))
    monkeypatch.setenv("OSIRIS_INSTRUMENT_DB_PATH", str(tmp_path / "instruments.sqlite3"))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    monkeypatch.delenv("OSIRIS_INSTRUMENT_FIXTURE", raising=False)
    return path


def _limits(**overrides):
    values = dict(
        max_order_notional=100_000, max_position_notional=100_000,
        max_daily_loss=100_000, max_gross_exposure=100_000,
    )
    values.update(overrides)
    return RiskLimits(**values)


def _order(key, symbol, side, price, quantity="1", working=False):
    assert submit_paper_order(
        client_order_id=key, symbol=symbol, side=side, quantity=quantity, price=price,
        fee="0", observed_at=FRESH, now=NOW, risk_service_available=True,
        kill_switch_active=False, working=working,
    )["status"] in {"filled", "accepted"}


def test_live_execution_stays_disabled():
    assert LIVE_EXECUTION_ENABLED is False


def test_a_gain_has_no_drawdown_and_ignores_nothing_on_the_book(book):
    initialize_paper_account("100000", _limits())
    _order("btc", "BTC", "BUY", "100")
    mark_to_market({"BTC": "110"}, observed_at=FRESH, now=NOW)
    before = snapshot()
    report = paper_capital_usage(NOW)
    assert report["state"] == "ready"
    assert report["equity"] == "100010"
    assert report["gross_exposure"] == "110"
    assert report["drawdown_from_inception"] == "0"
    assert report["adverse_pnl"] == "0"
    assert report["stop_breached"] is False
    assert report["stop_reasons"] == []
    assert report["strategy_budgets_enforced"] is False
    assert report["caller_allocation_used"] is False
    assert report["book_mutated"] is False
    assert report["live_execution"] is False
    assert snapshot() == before


def test_a_mark_down_breaches_the_stored_loss_limit(book):
    initialize_paper_account("100000", _limits(max_daily_loss=10))
    _order("btc", "BTC", "BUY", "100")
    mark_to_market({"BTC": "80"}, observed_at=FRESH, now=NOW)
    report = paper_capital_usage(NOW)
    assert report["state"] == "ready"
    assert report["equity"] == "99980"
    assert report["cash"] == "99900"
    assert report["adverse_pnl"] == "-20"
    assert report["loss_headroom"] == "-10"
    assert report["drawdown_from_inception"] == "0.0002"
    assert report["stop_breached"] is True
    assert report["stop_reasons"] == ["daily_loss_limit_reached"]


def test_a_higher_mark_can_breach_gross_and_position_limits(book):
    initialize_paper_account("100000", _limits(max_gross_exposure=50, max_position_notional=50))
    _order("btc", "BTC", "BUY", "40")
    mark_to_market({"BTC": "80"}, observed_at=FRESH, now=NOW)
    report = paper_capital_usage(NOW)
    assert report["gross_exposure"] == "80"
    assert report["exposure_headroom"] == "-30"
    assert report["positions"] == [{
        "symbol": "BTC",
        "quantity": "1",
        "mark": "80",
        "inventory_notional": "80",
        "open_buy_notional": "0",
        "position_notional": "80",
        "position_limit_breached": True,
    }]
    assert report["stop_reasons"] == [
        "exposure_budget_exceeded", "max_position_notional_exceeded",
    ]


def test_a_resting_buy_counts_toward_gross_without_a_mark(book):
    initialize_paper_account("100000", _limits())
    _order("bid", "BTC", "BUY", "30", working=True)
    report = paper_capital_usage(NOW)
    assert report["state"] == "ready"
    assert report["equity"] == "100000"
    assert report["gross_exposure"] == "30"
    assert report["drawdown_from_inception"] == "0"
    assert report["positions"][0]["open_buy_notional"] == "30"
    assert report["positions"][0]["mark"] is None


def test_missing_mark_does_not_publish_usage(book):
    initialize_paper_account("100000", _limits())
    _order("btc", "BTC", "BUY", "100")
    report = paper_capital_usage(NOW)
    assert report["state"] == "incomplete"
    assert report["reason"] == "mark_required"
    assert report["equity"] is None
    assert report["gross_exposure"] is None
    assert report["stop_breached"] is None
    assert report["positions"] == []


def test_corrupt_book_is_unavailable(book):
    initialize_paper_account("100000", _limits())
    _order("btc", "BTC", "BUY", "100")
    with sqlite3.connect(book) as conn:
        conn.execute("UPDATE paper_exec_audit SET payload='{}' WHERE seq=1")
    report = paper_capital_usage(NOW)
    assert report["state"] == "unavailable"
    assert report["reason"] == "audit_chain_corrupt"
    assert report["equity"] is None


def test_http_ignores_caller_budgets(book):
    initialize_paper_account("100000", _limits(max_daily_loss=10))
    _order("btc", "BTC", "BUY", "100")
    mark_to_market({"BTC": "80"}, observed_at=FRESH, now=NOW)
    response = TestClient(app).get("/paper/execution/capital", params={
        "as_of": NOW, "strategies": "alpha", "budget": "1", "drawdown": "0",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["drawdown_from_inception"] == "0.0002"
    assert body["caller_allocation_used"] is False
    assert body["strategy_budgets_enforced"] is False
    assert "alpha" not in response.text
