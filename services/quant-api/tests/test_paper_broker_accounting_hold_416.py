"""Unaccounted external fills must not appear as a clear canonical risk book."""

import pytest
from test_paper_broker_reservations_416 import Transport, _broker
from test_pretrade_risk_gateway_416 import FAVORABLE, _order, open_book

from osiris_api import paper_execution as ledger
from osiris_api.control_plane.database import Database, digest

REASON = "broker_fills_require_accounting"


@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("status,filled", [
    ("partially_filled", "0.5"),
    ("filled", "1"),
    ("canceled", "0.5"),
    ("expired", "0.5"),
    ("done_for_day", "0.5"),
])
def test_external_fills_make_loss_policy_and_valuation_unavailable(
    monkeypatch, tmp_path, side, status, filled,
):
    monkeypatch.delenv("OSIRIS_CONTROL_DB", raising=False)
    stamp = open_book(monkeypatch, tmp_path, shares="3" if side == "SELL" else "0")
    before = ledger.snapshot()
    assert ledger.read_risk_limits()["status"] == "available"
    assert ledger.daily_loss_gate(now=stamp)["state"] == "clear"
    assert ledger.account_valuation(before)["marks_available"] is True

    transport = Transport()
    service = _broker(tmp_path, transport)
    service.submit(_order(side=side), **FAVORABLE)
    transport.change(status=status, filled=filled)
    service.reconcile("order-1")
    held = ledger.snapshot()
    assert held["broker_accounting_pending"] is True
    # Cumulative broker averages still create no execution IDs or accounting.
    assert held["positions"] == before["positions"]
    assert held["cash"] == before["cash"]
    assert held["fill_count"] == before["fill_count"]

    context = ledger.execution_context(symbol="TEST", price="100", side=side, now=stamp)
    assert context["daily_loss_ready"] is False
    assert context["adverse_daily_pnl"] is None
    assert context["new_risk_allowed"] is False
    assert context["reason"] == REASON
    gate = ledger.daily_loss_gate(now=stamp)
    assert gate["state"] == "unavailable"
    assert gate["reason"] == REASON
    assert gate["adverse_daily_pnl"] is None
    assert gate["unrealized_pnl"] is None
    policy = ledger.read_risk_limits()
    assert policy["status"] == "unavailable"
    assert policy["reason"] == REASON
    assert policy["limits"] is None
    valuation = ledger.account_valuation(held)
    assert valuation["market_value"] is None
    assert valuation["pnl"]["equity"] is None
    assert valuation["pnl"]["unrealized"] is None
    assert valuation["pnl"]["realized"] == held["realized_pnl"]
    assert valuation["marks_available"] is False
    assert REASON in valuation["valuation_warnings"]
    assert ledger.snapshot()["audit"] == held["audit"]


def test_zero_fill_terminal_order_keeps_readouts_available(monkeypatch, tmp_path):
    monkeypatch.delenv("OSIRIS_CONTROL_DB", raising=False)
    stamp = open_book(monkeypatch, tmp_path)
    transport = Transport()
    service = _broker(tmp_path, transport)
    service.submit(_order(), **FAVORABLE)
    transport.change(status="canceled")
    service.reconcile("order-1")
    account = ledger.snapshot()
    assert account["broker_accounting_pending"] is False
    assert ledger.daily_loss_gate(now=stamp)["state"] == "clear"
    assert ledger.read_risk_limits()["status"] == "available"
    assert ledger.account_valuation(account)["marks_available"] is True
    assert ledger.execution_context(symbol="TEST", price="100", now=stamp)["daily_loss_ready"] is True


def test_loss_gate_hides_clear_state_for_unreserved_configured_inventory(monkeypatch, tmp_path):
    monkeypatch.delenv("OSIRIS_CONTROL_DB", raising=False)
    stamp = open_book(monkeypatch, tmp_path)
    control = Database(str(tmp_path / "legacy.sqlite3"))
    control.put("broker-orders", "order-1", {
        "fingerprint": digest(_order()), "status": "submitting", "order": _order(),
    })
    monkeypatch.setenv("OSIRIS_CONTROL_DB", control.path)
    gate = ledger.daily_loss_gate(now=stamp)
    assert gate["state"] == "unavailable"
    assert gate["reason"] == "broker_reservation_required_reconcile_first"
    assert gate["adverse_daily_pnl"] is None
