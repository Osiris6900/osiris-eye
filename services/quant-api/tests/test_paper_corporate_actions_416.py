"""Stored master corporate actions adjust the paper book, or they do not apply.

A 2026 RELIANCE buy is after the 2024 split, so applying due actions leaves it
alone. Shares bought before the effective time are split or credited. The
caller cannot supply the ratio.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app
from osiris_api.paper_corporate_actions import apply_paper_corporate_actions
from osiris_api.paper_execution import (
    LIVE_EXECUTION_ENABLED,
    cancel_paper_order,
    initialize_paper_account,
    mark_to_market,
    reconcile,
    snapshot,
    submit_paper_order,
)
from osiris_api.risk import RiskLimits

LIMITS = RiskLimits(
    max_order_notional=100_000, max_position_notional=100_000,
    max_daily_loss=100_000, max_gross_exposure=100_000,
)


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "paper.sqlite3"))
    monkeypatch.setenv("OSIRIS_INSTRUMENT_DB_PATH", str(tmp_path / "instruments.sqlite3"))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    monkeypatch.delenv("OSIRIS_INSTRUMENT_FIXTURE", raising=False)
    initialize_paper_account("100000", LIMITS)
    return tmp_path


def _buy(symbol, now, *, price, quantity="1", key="order-1", working=False):
    return submit_paper_order(
        client_order_id=key, symbol=symbol, side="BUY", quantity=quantity, price=price,
        fee="0", observed_at=now, now=now, risk_service_available=True,
        kill_switch_active=False, working=working,
    )


def test_live_execution_stays_disabled():
    assert LIVE_EXECUTION_ENABLED is False


def test_later_purchase_is_not_split(book):
    bought = _buy("RELIANCE", "2026-11-23T04:30:00+00:00", price="100.05", quantity="2")
    assert bought["status"] == "filled"
    before = snapshot()
    result = apply_paper_corporate_actions("2026-11-23T05:00:00+00:00")
    assert result["live_execution"] is False
    assert all(row["action_type"] != "split" for row in result["applied"])
    assert any(row["reason"] == "position_crosses_effective_time" for row in result["skipped"])
    assert snapshot()["positions"] == before["positions"]
    assert snapshot()["cash"] == before["cash"]
    assert reconcile()["passed"] is True


def test_split_doubles_shares_and_conserves_cost(book):
    bought = _buy("RELIANCE", "2024-05-01T04:30:00+00:00", price="100.05", quantity="2")
    assert bought["status"] == "filled"
    cash = snapshot()["cash"]
    result = apply_paper_corporate_actions("2024-06-02T00:00:00+00:00")
    applied = [row for row in result["applied"] if row["action_type"] == "split"]
    assert len(applied) == 1
    position = snapshot()["positions"]
    assert position == [{"symbol": "RELIANCE", "quantity": "4", "average_cost": "50.025"}]
    assert snapshot()["cash"] == cash
    assert snapshot()["corporate_actions"][0]["action_type"] == "split"
    assert reconcile()["passed"] is True
    again = apply_paper_corporate_actions("2024-06-02T00:00:00+00:00")
    assert again["applied"] == []
    assert applied[0]["content_hash"] in again["unchanged"]
    assert snapshot()["positions"] == position


def test_cash_dividend_increases_cash_only(book):
    bought = _buy("AAPL", "2024-08-01T15:00:00+00:00", price="100.00", quantity="4")
    assert bought["status"] == "filled"
    before = snapshot()
    result = apply_paper_corporate_actions("2024-08-16T00:00:00+00:00")
    assert any(row["action_type"] == "cash_dividend" for row in result["applied"])
    assert snapshot()["positions"] == before["positions"]
    assert snapshot()["cash"] == "99601"
    assert snapshot()["realized_pnl"] == "1"
    assert reconcile()["passed"] is True


def test_open_order_and_mark_block_the_action(book):
    resting = _buy(
        "AAPL", "2024-08-01T15:00:00+00:00", price="100.00", quantity="1",
        key="resting", working=True,
    )
    assert resting["state"] == "accepted"
    blocked = apply_paper_corporate_actions("2024-08-16T00:00:00+00:00")
    assert any(row["reason"] == "open_order_blocks_corporate_action" for row in blocked["skipped"])
    assert snapshot()["positions"] == []
    cancel_paper_order(client_order_id="resting")
    _buy("AAPL", "2024-08-01T15:00:00+00:00", price="100.00", quantity="1", key="held")
    mark_to_market({"AAPL": "100.00"}, observed_at="2024-08-01T15:00:00+00:00", now="2024-08-01T15:00:00+00:00")
    marked = apply_paper_corporate_actions("2024-08-16T00:00:00+00:00")
    assert any(row["reason"] == "mark_blocks_corporate_action" for row in marked["skipped"])
    assert snapshot()["positions"] == [{"symbol": "AAPL", "quantity": "1", "average_cost": "100"}]
    assert reconcile()["passed"] is True


def test_crash_does_not_keep_a_partial_split(book):
    _buy("RELIANCE", "2024-05-01T04:30:00+00:00", price="100.05", quantity="2")
    before = snapshot()
    with pytest.raises(RuntimeError, match="simulated_crash"):
        apply_paper_corporate_actions("2024-06-02T00:00:00+00:00", simulate_crash=True)
    assert snapshot() == before
    apply_paper_corporate_actions("2024-06-02T00:00:00+00:00")
    assert snapshot()["positions"][0]["quantity"] == "4"
    assert reconcile()["passed"] is True


def test_future_actions_and_caller_economics_do_nothing(book):
    early = apply_paper_corporate_actions("2024-01-01T00:00:00+00:00")
    assert early["applied"] == []
    assert early["not_due"] == 2
    assert snapshot()["corporate_actions"] == []
    response = TestClient(app).post("/paper/execution/corporate-actions", json={
        "as_of": "2024-06-02T00:00:00+00:00", "ratio": "100",
    })
    assert response.status_code == 422
    assert response.json()["detail"] == "corporate_action_economics_not_accepted"
    assert snapshot()["corporate_actions"] == []


def test_flat_book_records_a_zero_application_and_a_later_buy_is_unsplit(book):
    first = apply_paper_corporate_actions("2026-01-01T00:00:00+00:00")
    assert len(first["applied"]) == 2
    assert snapshot()["positions"] == []
    assert snapshot()["cash"] == "100000"
    bought = _buy("RELIANCE", "2026-11-23T04:30:00+00:00", price="100.05", quantity="2")
    assert bought["status"] == "filled"
    assert snapshot()["positions"] == [{"symbol": "RELIANCE", "quantity": "2", "average_cost": "100.05"}]
    assert reconcile()["passed"] is True
