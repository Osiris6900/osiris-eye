"""A valid hash chain must agree with the account it purportedly describes."""
import json
import sqlite3
from datetime import UTC, datetime

import pytest

from osiris_api import admin_centre
from osiris_api.paper_execution import (
    JOURNAL_SCHEMA,
    _verify_audit,
    apply_paper_fill,
    cancel_paper_order,
    execution_context,
    initialize_paper_account,
    mark_to_market,
    reconcile,
    reconciliation_report,
    recover,
    set_kill_switch,
    snapshot,
    submit_paper_order,
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "paper.db"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(path))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    initialize_paper_account("1000")
    return path


def order(key="buy", **extra):
    stamp = datetime.now(UTC).isoformat()
    return submit_paper_order(**{
        "client_order_id": key, "symbol": "BTC", "side": "BUY", "quantity": "2",
        "price": "100", "fee": "2", "observed_at": stamp, "now": stamp, **extra,
    })


def fill(key="buy", fill_id="part", quantity="1", **extra):
    stamp = datetime.now(UTC).isoformat()
    return apply_paper_fill(client_order_id=key, client_fill_id=fill_id, quantity=quantity,
                            observed_at=stamp, now=stamp, **extra)


def mark(price="110"):
    stamp = datetime.now(UTC).isoformat()
    mark_to_market({"BTC": price}, observed_at=stamp, now=stamp)


def chain_is_intact(path):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        _verify_audit(conn)


def test_event_bound_round_trip_partial_fill_cancellation_and_controls(db):
    assert order(working=True)["state"] == "accepted"
    assert fill()["state"] == "partially_filled"
    mark()
    set_kill_switch(True)
    cancel_paper_order(client_order_id="buy")
    set_kill_switch(False)
    assert order("sell", side="SELL", quantity="1", price="120", fee="1")["state"] == "filled"
    report = reconciliation_report()
    assert report["passed"] is True
    assert report["journal_binding"] == JOURNAL_SCHEMA
    assert recover()["snapshot"]["cash"] == "1018"
    assert snapshot()["positions"] == []
    events = [json.loads(row["payload"]) for row in snapshot()["audit"] if row["event"] == "fill_recorded"]
    assert all(all(field in event for field in ("fee", "symbol", "side", "state")) for event in events)


@pytest.mark.parametrize("field,value", [
    ("max_order_notional", "999999"), ("max_position_notional", "999999"),
    ("max_daily_loss", "999999"), ("max_gross_exposure", "999999"),
    ("max_market_age_seconds", "999999"), ("max_future_skew_seconds", "999999"),
])
def test_unaudited_risk_policy_changes_block_new_orders(db, field, value):
    with sqlite3.connect(db) as conn:
        conn.execute(f"UPDATE paper_exec_meta SET {field}=? WHERE id=1", (value,))
    chain_is_intact(db)
    report = reconciliation_report()
    assert report["passed"] is False
    assert "journal_risk_policy" in report["breaks"]
    with pytest.raises(ValueError, match="reconciliation_break"):
        order()
    with pytest.raises(ValueError, match="reconciliation_break"):
        execution_context(symbol="BTC", price="100")
    assert snapshot()["fill_count"] == 0


def test_unaudited_kill_switch_clear_cannot_resume_execution(db):
    set_kill_switch(True)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE paper_exec_meta SET kill_switch=0")
    chain_is_intact(db)
    assert "journal_kill_switch" in reconcile()["breaks"]
    with pytest.raises(ValueError, match="reconciliation_break"):
        order()


def test_arithmetically_consistent_fee_cash_and_cost_basis_rewrite_is_detected(db):
    order(working=True)
    fill()
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE paper_exec_fills SET fee='0'")
        conn.execute("UPDATE paper_exec_meta SET cash='900'")
        conn.execute("UPDATE paper_exec_positions SET average_cost='100'")
    chain_is_intact(db)
    report = reconciliation_report()
    assert "cash" not in report["breaks"]
    assert "position:BTC" not in report["breaks"]
    assert "journal_fills" in report["breaks"]
    assert report["passed"] is False
    assert admin_centre.paper_status()["cash"] is None
    with pytest.raises(ValueError, match="reconciliation_break"):
        fill(fill_id="another")


@pytest.mark.parametrize("field,value", [
    ("price", "101"), ("fee", "3"), ("reason", "forged-approval"),
    ("risk_decision_id", "unrelated-decision"), ("limits_evaluated", 0),
    ("created_at", "2000-01-01T00:00:00Z"),
])
def test_order_projection_cannot_diverge_from_its_risk_decision(db, field, value):
    order(working=True)
    with sqlite3.connect(db) as conn:
        conn.execute(f"UPDATE paper_exec_orders SET {field}=?", (value,))
    chain_is_intact(db)
    assert "journal_order:buy" in reconcile()["breaks"]
    with pytest.raises(ValueError, match="reconciliation_break"):
        order()


def test_unrecorded_cancellation_cannot_release_the_order_reservation(db):
    order(working=True)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE paper_exec_orders SET state='cancelled',status='cancelled',leaves_quantity='0'")
    chain_is_intact(db)
    assert "journal_order:buy" in reconcile()["breaks"]
    with pytest.raises(ValueError, match="reconciliation_break"):
        order("spend-released-cash", quantity="9", fee="0")


def test_erasing_a_rejected_order_cannot_remove_the_decision_history(db):
    order(quantity="9999")
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM paper_exec_orders")
    chain_is_intact(db)
    assert "journal_order_inventory" in reconcile()["breaks"]


def test_erasing_balanced_orders_and_fills_cannot_reset_account_history(db):
    order(quantity="1", fee="0")
    order("sell", side="SELL", quantity="1", fee="0")
    assert snapshot()["cash"] == "1000"
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM paper_exec_fills")
        conn.execute("DELETE FROM paper_exec_orders")
    chain_is_intact(db)
    report = reconcile()
    assert report["passed"] is False
    assert "journal_fills" in report["breaks"]


@pytest.mark.parametrize("change", ["timestamp", "price_and_unrealized", "delete"])
def test_unaudited_mark_changes_cannot_fabricate_a_fresh_valuation(db, change):
    order(quantity="1", fee="0")
    mark()
    with sqlite3.connect(db) as conn:
        if change == "timestamp":
            conn.execute("UPDATE paper_exec_marks SET observed_at='2099-01-01T00:00:00Z'")
        elif change == "price_and_unrealized":
            conn.execute("UPDATE paper_exec_marks SET price='120'")
            conn.execute("UPDATE paper_exec_meta SET unrealized_pnl='20'")
        else:
            conn.execute("DELETE FROM paper_exec_marks")
            conn.execute("UPDATE paper_exec_meta SET unrealized_pnl='0'")
    chain_is_intact(db)
    assert "journal_marks" in reconcile()["breaks"]
    assert admin_centre.paper_status()["pnl"] is None


def test_changing_initial_cash_and_cash_together_is_still_detected(db):
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE paper_exec_meta SET initial_cash='2000',cash='2000'")
    chain_is_intact(db)
    assert "journal_initial_cash" in reconcile()["breaks"]


@pytest.mark.parametrize("flag", [None, 0, 1, "false", "true", [], {}])
def test_only_literal_false_can_leave_the_per_order_kill_switch_clear(db, flag):
    result = order(kill_switch_active=flag)
    assert result["state"] == "rejected"
    assert result["reason"] == "kill_switch_active"
    assert snapshot()["fill_count"] == 0
    assert reconcile()["passed"] is True


@pytest.mark.parametrize("flag", [None, 0, 1, "false", "true", [], {}])
def test_ambiguous_live_flags_are_refused_for_orders_fills_and_cancels(db, flag):
    order(working=True)
    for action in (lambda: order("other", live_execution=flag),
                   lambda: fill(live_execution=flag),
                   lambda: cancel_paper_order(client_order_id="buy", live_execution=flag)):
        with pytest.raises(ValueError, match="live_execution_disabled"):
            action()
    assert snapshot()["cash"] == "1000"
    assert snapshot()["fill_count"] == 0
    assert reconcile()["passed"] is True


@pytest.mark.parametrize("flag", [None, 0, 1, "false", "true", [], {}])
def test_ambiguous_working_mode_cannot_turn_a_resting_order_into_an_immediate_fill(db, flag):
    with pytest.raises(ValueError, match="working_state_invalid"):
        order(working=flag)
    assert snapshot()["fill_count"] == 0


def test_failed_fill_rolls_back_both_projection_and_journal(db):
    order(working=True)
    before = snapshot()
    with pytest.raises(RuntimeError, match="simulated_crash"):
        fill(simulate_crash=True)
    after = snapshot()
    assert after["cash"] == before["cash"]
    assert after["audit"] == before["audit"]
    assert fill()["state"] == "partially_filled"
    assert reconcile()["passed"] is True
