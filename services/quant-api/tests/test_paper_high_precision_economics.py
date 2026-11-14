"""Allowed decimal inputs retain economics beyond the default Decimal context."""
import json
import os
import subprocess
import sys
from fractions import Fraction

import pytest

from osiris_api import paper_execution as ledger
from osiris_api.risk import RiskLimits

NOW = "2026-11-14T04:30:00+00:00"
TINY = "0.0000000000000000000000000001"
ABOVE_ONE = "1.0000000000000000000000000001"


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "precise.sqlite3"))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)


def order(key, *, side="BUY", quantity="1", price="1", symbol="BTC", **kwargs):
    return ledger.submit_paper_order(
        client_order_id=key, symbol=symbol, side=side, quantity=quantity,
        price=price, observed_at=NOW, now=NOW, **kwargs)


def fill(key, fill_id, quantity):
    return ledger.apply_paper_fill(
        client_order_id=key, client_fill_id=fill_id, quantity=quantity,
        observed_at=NOW, now=NOW)


def assert_flat_conservation(state):
    assert state["positions"] == []
    assert Fraction(state["cash"]) == Fraction(state["initial_cash"]) + Fraction(state["realized_pnl"])
    assert state["unrealized_pnl"] == "0"
    assert ledger.reconciliation_report()["passed"] is True


def test_high_precision_round_trip_retains_cash_profit(account):
    ledger.initialize_paper_account("1000")
    assert order("buy", price=ABOVE_ONE)["state"] == "filled"
    assert Fraction(ledger.snapshot()["cash"]) == Fraction("1000") - Fraction(ABOVE_ONE)
    assert order("sell", side="SELL", price="1.0000000000000000000000000002")["state"] == "filled"
    state = ledger.recover()["snapshot"]
    assert state["realized_pnl"] == TINY
    assert_flat_conservation(state)


def test_full_supported_256_digit_coefficients_keep_representable_cash_exact(account):
    ledger.initialize_paper_account("2")
    buy_price = "1." + "0" * 254 + "1"
    sell_price = "1." + "0" * 254 + "2"
    assert order("buy", price=buy_price)["state"] == "filled"
    assert order("sell", side="SELL", price=sell_price)["state"] == "filled"
    state = ledger.recover()["snapshot"]
    assert Fraction(state["realized_pnl"]) == Fraction(sell_price) - Fraction(buy_price)
    assert_flat_conservation(state)


def test_high_precision_partials_fees_and_separate_process_recovery(account):
    ledger.initialize_paper_account("1000")
    fee = "0.0000000000000000000000000003"
    assert order("buy", quantity=ABOVE_ONE, fee=fee, working=True)["state"] == "accepted"
    assert fill("buy", "first", "1")["state"] == "partially_filled"
    assert fill("buy", "tiny-rest", TINY)["state"] == "filled"
    assert ledger.snapshot()["positions"][0]["quantity"] == ABOVE_ONE
    assert order("sell", side="SELL", quantity=ABOVE_ONE)["state"] == "filled"
    state = ledger.recover()["snapshot"]
    assert Fraction(state["realized_pnl"]) == -Fraction(fee)
    assert_flat_conservation(state)
    child = subprocess.run(
        [sys.executable, "-c", "import json; from osiris_api import paper_execution as p; print(json.dumps(p.recover()))"],
        check=True, capture_output=True, text=True, timeout=30, env=os.environ.copy())
    recovered = json.loads(child.stdout)
    assert recovered["passed"] is True
    assert recovered["snapshot"] == state


def test_order_notional_limit_rejects_sub_context_overage(account):
    ledger.initialize_paper_account("1000", RiskLimits(max_order_notional=100))
    result = order("over", price="100.00000000000000000000000001")
    assert result["state"] == "rejected"
    assert result["reason"] == "max_order_notional_exceeded"
    assert ledger.snapshot()["cash"] == "1000"


@pytest.mark.parametrize("limit", ["position", "gross"])
@pytest.mark.parametrize("working", [False, True])
def test_held_and_reserved_exposure_retains_sub_context_overage(account, limit, working):
    policy = {"max_position_notional": 1} if limit == "position" else {"max_gross_exposure": 1}
    ledger.initialize_paper_account("1000", RiskLimits(**policy))
    assert order("held", working=working)["state"] == ("accepted" if working else "filled")
    result = order("tiny-over", quantity=TINY)
    assert result["state"] == "rejected"
    assert result["reason"] == ("max_position_notional_exceeded" if limit == "position" else "exposure_budget_exceeded")


def test_unrealized_loss_and_operator_view_retain_sub_context_breach(account):
    ledger.initialize_paper_account("1000", RiskLimits(max_daily_loss=0.9))
    assert order("held", quantity=ABOVE_ONE)["state"] == "filled"
    ledger.mark_to_market({"BTC": "0.1"}, observed_at=NOW, now=NOW)
    gate = ledger.daily_loss_gate(now=NOW)
    assert gate["state"] == "limit_reached"
    assert Fraction(gate["unrealized_pnl"]) < -Fraction("0.9")
    result = order("new-risk", symbol="ETH")
    assert result["state"] == "rejected"
    assert result["reason"] == "daily_loss_limit_reached"
