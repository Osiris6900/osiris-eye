"""Current account risk, independent reconciliation, and Decimal cash identities."""
import json
import os
import sqlite3
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from osiris_api import paper_execution as ledger
from osiris_api.paper_execution import (
    apply_paper_fill,
    execution_context,
    execution_status,
    get_paper_order,
    initialize_paper_account,
    mark_to_market,
    reconcile,
    recover,
    snapshot,
    submit_paper_order,
)
from osiris_api.risk import RiskLimits

NOW = "2026-11-11T16:00:00+00:00"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "accounting.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(path))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    return path


def order(key, symbol="BTC", side="BUY", quantity="1", price="100", **extra):
    return submit_paper_order(
        client_order_id=key, symbol=symbol, side=side, quantity=quantity, price=price,
        observed_at=NOW, now=NOW, **extra,
    )


def fill(key, fill_key, quantity="1", **extra):
    return apply_paper_fill(
        client_order_id=key, client_fill_id=fill_key, quantity=quantity,
        observed_at=NOW, now=NOW, **extra,
    )


def test_working_buy_rechecks_loss_limit_after_another_position_is_closed(db):
    initialize_paper_account("1000", RiskLimits(max_daily_loss=10))
    assert order("inventory", symbol="ETH")["status"] == "filled"
    mark_to_market({"ETH": "100"}, observed_at=NOW, now=NOW)
    assert order("pending", working=True)["status"] == "accepted"
    assert order("loss", symbol="ETH", side="SELL", price="50")["status"] == "filled"
    before = snapshot()
    with pytest.raises(ValueError, match="daily_loss_limit_reached"):
        fill("pending", "after-loss")
    state = recover()["snapshot"]
    assert state["cash"] == before["cash"] == "950"
    assert state["fill_count"] == before["fill_count"] == 2
    assert state["positions"] == []
    assert state["open_orders"][0]["leaves_quantity"] == "1"
    assert reconcile()["passed"] is True


def test_gross_risk_uses_other_positions_current_marks(db):
    initialize_paper_account("1000", RiskLimits(max_gross_exposure=250))
    assert order("held", symbol="ETH")["status"] == "filled"
    mark_to_market({"ETH": "200"}, observed_at=NOW, now=NOW)
    refused = order("new-risk")
    assert refused["reason"] == "exposure_budget_exceeded"
    assert snapshot()["cash"] == "900"
    assert snapshot()["fill_count"] == 1
    context = execution_context(symbol="BTC", price="100")
    assert context["current_gross_exposure"] == "200"
    assert context["current_position_notional"] == "0"
    assert context["limits"]["max_gross_exposure"] == 250
    assert snapshot()["marks"] == [{"symbol": "ETH", "price": "200", "observed_at": NOW}]


def test_working_fill_rechecks_changed_gross_exposure_without_double_reserving(db):
    initialize_paper_account("1000", RiskLimits(max_gross_exposure=250))
    assert order("held", symbol="ETH")["status"] == "filled"
    mark_to_market({"ETH": "100"}, observed_at=NOW, now=NOW)
    assert order("pending", working=True)["status"] == "accepted"
    context = execution_context(symbol="BTC", price="100")
    assert context["current_gross_exposure"] == "200"
    assert context["current_position_notional"] == "100"
    mark_to_market({"ETH": "200"}, observed_at=NOW, now=NOW)
    with pytest.raises(ValueError, match="exposure_budget_exceeded"):
        fill("pending", "too-much")
    mark_to_market({"ETH": "150"}, observed_at=NOW, now=NOW)
    assert fill("pending", "at-budget")["status"] == "filled"
    assert snapshot()["cash"] == "800"
    assert reconcile()["passed"] is True


def test_working_fill_fails_closed_if_current_risk_check_raises(db, monkeypatch):
    initialize_paper_account("1000")
    order("pending", working=True)

    def unavailable(**kwargs):
        raise RuntimeError("risk unavailable")

    monkeypatch.setattr("osiris_api.paper_execution.check_execution", unavailable)
    with pytest.raises(ValueError, match="risk_service_unavailable"):
        fill("pending", "unavailable")
    assert snapshot()["cash"] == "1000"
    assert snapshot()["fill_count"] == 0
    assert reconcile()["passed"] is True


def test_reconcile_rejects_audit_corruption_even_when_balances_match(db):
    initialize_paper_account("1000")
    order("buy")
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE paper_exec_audit SET payload='{}' WHERE seq=2")
    assert reconcile()["passed"] is False
    assert reconcile()["breaks"] == ["audit_chain_corrupt"]


def test_reconcile_detects_fill_associated_with_wrong_order_symbol(db):
    initialize_paper_account("1000")
    order("buy")
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE paper_exec_orders SET symbol='ETH' WHERE client_order_id='buy'")
    report = reconcile()
    assert report["passed"] is False
    assert "fill_order_mismatch:buy" in report["breaks"]


def test_partial_round_trip_fees_marks_and_restart_preserve_equity_identity(db):
    initialize_paper_account("1000")
    assert order("buy", quantity="3", price="100", fee="0.3", working=True)["state"] == "accepted"
    assert fill("buy", "buy-part", quantity="1", price="99")["state"] == "partially_filled"
    assert fill("buy", "buy-rest", quantity="2")["state"] == "filled"
    assert order("sell-part", side="SELL", quantity="1", price="110", fee="0.1")["state"] == "filled"
    mark_to_market({"BTC": "105"}, observed_at=NOW, now=NOW)
    state = recover()["snapshot"]
    quantity = Decimal(state["positions"][0]["quantity"])
    equity = Decimal(state["cash"]) + quantity * Decimal(105)
    total_pnl = Decimal(state["realized_pnl"]) + Decimal(state["unrealized_pnl"])
    assert equity == Decimal(state["initial_cash"]) + total_pnl
    assert order("close", side="SELL", quantity="2", price="105", fee="0.2")["state"] == "filled"
    state = recover()["snapshot"]
    assert state["positions"] == []
    assert state["unrealized_pnl"] == "0"
    assert Decimal(state["cash"]) == Decimal(state["initial_cash"]) + Decimal(state["realized_pnl"])
    assert reconcile()["passed"] is True


def test_closing_fill_settles_repeating_cost_basis_residual_after_crash(db):
    initialize_paper_account("1000")
    order("buy", quantity="3", price="102", fee="0.3", working=True)
    fill("buy", "first", quantity="1", price="101")
    fill("buy", "rest", quantity="2")
    order("sell-1", side="SELL", price="99", fee="0.1")
    order("sell-2", side="SELL", price="98", fee="0.1")
    order("close", side="SELL", price="99", fee="0.1", working=True)
    before = snapshot()
    with pytest.raises(RuntimeError, match="simulated_crash"):
        fill("close", "closing", simulate_crash=True)
    assert recover()["snapshot"] == before
    fill("close", "closing")
    state = recover()["snapshot"]
    assert state["cash"] == "990.4"
    assert state["realized_pnl"] == "-9.6"
    assert state["positions"] == []
    # A separate instrument and another inventory cycle cannot inherit a residue.
    order("other-buy", symbol="ETH", quantity="3", price="10", fee="0.1")
    order("other-sell", symbol="ETH", quantity="3", side="SELL", price="11", fee="0.1")
    order("again-buy", price="100")
    order("again-sell", side="SELL", price="101")
    final = recover()["snapshot"]
    assert final["cash"] == "994.2"
    assert final["realized_pnl"] == "-5.8"
    assert reconcile()["passed"] is True


def test_existing_journal_without_accounting_model_retains_original_allocations(db):
    initialize_paper_account("1000")
    # Reconstruct an original v2 initialization, before it has any economics.
    with sqlite3.connect(db) as conn:
        payload = json.loads(conn.execute("SELECT payload FROM paper_exec_audit WHERE seq=1").fetchone()[0])
        del payload["accounting_model"]
        body = ledger._canon_json(payload)
        row_hash = ledger._row_hash("GENESIS", 1, "account_initialized", None, body)
        conn.execute("UPDATE paper_exec_audit SET payload=?,row_hash=? WHERE seq=1", (body, row_hash))
    order("buy", quantity="3", price="102", fee="0.3", working=True)
    fill("buy", "first", quantity="1", price="101")
    fill("buy", "rest", quantity="2")
    for key, price in (("one", "99"), ("two", "98"), ("three", "99")):
        order(key, side="SELL", price=price, fee="0.1")
    state = recover()["snapshot"]
    assert state["cash"] == "990.4"
    assert state["realized_pnl"] == "-9.6000000000000000000000001"
    assert reconcile()["passed"] is True


def test_exact_risk_limits_cannot_be_bypassed_by_float_rounding(db):
    initialize_paper_account("1000", RiskLimits(max_order_notional=100))
    refused = order("just-over", price="100.000000000000000001")
    assert refused["reason"] == "max_order_notional_exceeded"
    assert refused["fill"] is None
    assert snapshot()["cash"] == "1000"


def test_order_lookup_and_atomic_status_include_terminal_outcome(db):
    initialize_paper_account("1000")
    assert get_paper_order("absent") is None
    submitted = order("buy", fee="0.1")
    persisted = get_paper_order("buy")
    assert persisted["risk_decision_id"] == submitted["risk_decision_id"]
    assert persisted["symbol"] == "BTC"
    assert persisted["quantity"] == "1"
    assert persisted["price"] == "100"
    assert persisted["fee"] == "0.1"
    assert persisted["execution_mode"] == "immediate"
    status = execution_status()
    assert status["reconciliation"]["passed"] is True
    assert status["account"]["cash"] == "899.9"
    assert status["account"]["orders"][0]["state"] == "filled"
    assert status["account"]["orders"][0]["client_order_id"] == "buy"
    assert status["account"]["open_orders"] == []


def test_atomic_status_reports_missing_account_and_corrupt_audit(db):
    assert execution_status()["reconciliation"]["breaks"] == ["account_not_initialized"]
    initialize_paper_account("1000")
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE paper_exec_audit SET payload='{}' WHERE seq=1")
    assert execution_status()["reconciliation"]["breaks"] == ["audit_chain_corrupt"]
    with pytest.raises(ValueError, match="audit_chain_corrupt"):
        get_paper_order("absent")


def test_concurrent_process_submission_and_restart_create_exactly_one_fill(db):
    """SQLite serialization, rather than a process-local lock, must enforce replay."""
    initialize_paper_account("1000")
    source = Path(__file__).resolve().parents[1] / "src"
    environment = {**os.environ, "PYTHONPATH": str(source)}
    code = (
        "import json; from osiris_api.paper_execution import submit_paper_order; "
        "print(json.dumps(submit_paper_order(client_order_id='concurrent', "
        "symbol='BTC',side='BUY',quantity='1',price='100',fee='0.1', "
        f"observed_at={NOW!r},now={NOW!r})))"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code], env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for _ in range(2)
    ]
    results = []
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            assert process.returncode == 0, stderr
            results.append(json.loads(stdout))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    assert sorted(result["status"] for result in results) == ["filled", "idempotent_replay"]
    assert results[0]["risk_decision_id"] == results[1]["risk_decision_id"]
    restarted = subprocess.run(
        [sys.executable, "-c", "import json; from osiris_api.paper_execution import recover; "
         "print(json.dumps(recover()))"], env=environment, capture_output=True,
        text=True, check=True, timeout=20,
    )
    state = json.loads(restarted.stdout)["snapshot"]
    assert state["fill_count"] == 1
    assert state["order_count"] == 1
    assert state["cash"] == "899.9"
    assert state["positions"] == [{"symbol": "BTC", "quantity": "1", "average_cost": "100.1"}]
