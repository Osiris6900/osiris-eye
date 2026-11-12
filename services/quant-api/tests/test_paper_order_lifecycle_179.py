"""Paper order lifecycle, partial fills, cancels, and reconciliation.

Live broker routing stays disabled. These tests exercise the simulation ledger.
"""
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app
from osiris_api.paper_execution import (
    _canon_json,
    _row_hash,
    apply_paper_fill,
    cancel_paper_order,
    initialize_paper_account,
    mark_to_market,
    order_transition,
    reconcile,
    reconciliation_report,
    recover,
    set_kill_switch,
    snapshot,
    submit_paper_order,
)
from osiris_api.risk import RiskLimits


NOW = "2026-11-08T16:00:00+00:00"
FRESH = "2026-11-08T15:59:30+00:00"
STALE = "2026-11-08T14:00:00+00:00"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "paper-lifecycle.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(path))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    return path


def _init():
    initialize_paper_account("1000", RiskLimits(
        max_order_notional=10_000, max_position_notional=10_000,
        max_daily_loss=1_000, max_gross_exposure=10_000,
    ))


def _working(client_order_id="buy-1", **overrides):
    payload = dict(
        client_order_id=client_order_id, symbol="BTC", side="BUY",
        quantity="10", price="100", fee="0", observed_at=FRESH, now=NOW, working=True,
    )
    payload.update(overrides)
    return submit_paper_order(**payload)


def _fill(client_order_id="buy-1", client_fill_id="fill-1", quantity="4", **overrides):
    payload = dict(
        client_order_id=client_order_id, client_fill_id=client_fill_id, quantity=quantity,
        observed_at=FRESH, now=NOW,
    )
    payload.update(overrides)
    return apply_paper_fill(**payload)


def test_transition_table_accepts_only_legal_moves():
    legal = {
        "new": {"accepted", "rejected"},
        "accepted": {"partially_filled", "filled", "cancelled"},
        "partially_filled": {"partially_filled", "filled", "cancelled"},
    }
    terminal = ("filled", "cancelled", "rejected")
    for current, targets in legal.items():
        for target in targets:
            assert order_transition(current, target) == target
        for target in ("new", *terminal):
            if target in targets:
                continue
            with pytest.raises(ValueError, match="invalid_order_transition"):
                order_transition(current, target)
    for current in terminal:
        for target in ("new", "accepted", "partially_filled", "filled", "cancelled", "rejected"):
            with pytest.raises(ValueError, match="invalid_order_transition"):
                order_transition(current, target)


def test_working_order_rests_without_moving_cash(db):
    _init()
    opened = _working()
    assert opened["status"] == "accepted"
    assert opened["state"] == "accepted"
    assert opened["filled_quantity"] == "0"
    assert opened["leaves_quantity"] == "10"
    assert opened["fill"] is None
    assert opened["live_execution"] is False
    assert opened["broker_order_submitted"] is False
    assert snapshot()["cash"] == "1000"
    assert snapshot()["fill_count"] == 0
    assert snapshot()["open_orders"][0]["client_order_id"] == "buy-1"
    assert reconcile()["passed"] is True


def test_partial_fill_then_completion_updates_cash_and_position(db):
    _init()
    _working()
    first = _fill()
    assert first["state"] == "partially_filled"
    assert first["filled_quantity"] == "4"
    assert first["leaves_quantity"] == "6"
    assert first["fill"]["quantity"] == "4"
    assert snapshot()["cash"] == "600"
    assert snapshot()["positions"] == [{"symbol": "BTC", "quantity": "4", "average_cost": "100"}]
    done = _fill(client_fill_id="fill-2", quantity="6")
    assert done["status"] == "filled"
    assert done["leaves_quantity"] == "0"
    assert snapshot()["cash"] == "0"
    assert snapshot()["open_orders"] == []
    assert snapshot()["fill_count"] == 2
    report = reconciliation_report()
    assert report["passed"] is True
    assert report["mismatches"] == []
    assert report["fields"][0]["expected"] == "0"
    assert report["fields"][0]["recorded"] == "0"
    assert report["daily_loss_basis"] == "realized_pnl"
    assert report["live_execution"] is False


def test_overfill_and_worse_price_leave_the_order_open(db):
    _init()
    _working()
    with pytest.raises(ValueError, match="invalid_fill_quantity"):
        _fill(quantity="11")
    with pytest.raises(ValueError, match="fill_price_exceeds_limit"):
        _fill(client_fill_id="too-high", quantity="1", price="101")
    assert snapshot()["cash"] == "1000"
    assert snapshot()["open_orders"][0]["state"] == "accepted"
    assert snapshot()["audit"][-1]["event"] == "fill_rejected"
    assert reconcile()["passed"] is True


def test_fill_replay_and_conflict(db):
    _init()
    _working()
    first = _fill()
    replay = _fill()
    assert replay["status"] == "idempotent_replay"
    assert replay["fill"]["seq"] == first["fill"]["seq"]
    assert snapshot()["fill_count"] == 1
    with pytest.raises(ValueError, match="idempotency_key_conflict"):
        _fill(quantity="5")
    assert snapshot()["fill_count"] == 1
    assert snapshot()["cash"] == "600"


def test_crash_mid_partial_fill_rolls_back_and_retries_once(db):
    _init()
    _working()
    with pytest.raises(RuntimeError, match="simulated_crash_before_commit"):
        _fill(simulate_crash=True)
    assert snapshot()["cash"] == "1000"
    assert snapshot()["fill_count"] == 0
    assert snapshot()["open_orders"][0]["state"] == "accepted"
    assert recover()["passed"] is True
    filled = _fill()
    assert filled["state"] == "partially_filled"
    replay = _fill()
    assert replay["status"] == "idempotent_replay"
    assert snapshot()["fill_count"] == 1
    assert snapshot()["cash"] == "600"
    assert reconcile()["passed"] is True


def test_cancel_releases_the_remainder_and_duplicate_cancel_replays(db):
    _init()
    _working()
    _fill()
    cancelled = cancel_paper_order(client_order_id="buy-1")
    assert cancelled["state"] == "cancelled"
    assert cancelled["filled_quantity"] == "4"
    assert cancelled["leaves_quantity"] == "0"
    assert cancelled["live_execution"] is False
    assert snapshot()["cash"] == "600"
    assert snapshot()["open_orders"] == []
    again = cancel_paper_order(client_order_id="buy-1")
    assert again["status"] == "idempotent_replay"
    assert again["original_status"] == "cancelled"
    events = [row["event"] for row in snapshot()["audit"]]
    assert events.count("order_cancelled") == 1
    assert events.count("cancel_replay") == 1
    assert reconcile()["passed"] is True


def test_crash_mid_cancel_keeps_the_order_open(db):
    _init()
    _working()
    with pytest.raises(RuntimeError, match="simulated_crash_before_commit"):
        cancel_paper_order(client_order_id="buy-1", simulate_crash=True)
    assert snapshot()["open_orders"][0]["state"] == "accepted"
    assert cancel_paper_order(client_order_id="buy-1")["state"] == "cancelled"
    assert snapshot()["open_orders"] == []


def test_cancel_of_a_filled_order_is_rejected_and_audited(db):
    _init()
    _working(quantity="1")
    assert _fill(quantity="1")["state"] == "filled"
    with pytest.raises(ValueError, match="invalid_order_transition"):
        cancel_paper_order(client_order_id="buy-1")
    assert snapshot()["audit"][-1]["event"] == "invalid_order_transition"
    assert snapshot()["cash"] == "900"
    with pytest.raises(ValueError, match="invalid_order_transition"):
        _fill(client_fill_id="late", quantity="1")


def test_cancel_racing_a_fill_stays_consistent(db):
    _init()
    _working()
    barrier = threading.Barrier(2)
    outcomes = {}

    def fill():
        barrier.wait()
        try:
            outcomes["fill"] = apply_paper_fill(
                client_order_id="buy-1", client_fill_id="race-fill", quantity="4",
                observed_at=FRESH, now=NOW,
            )
        except ValueError as exc:
            outcomes["fill_error"] = str(exc)

    def cancel():
        barrier.wait()
        try:
            outcomes["cancel"] = cancel_paper_order(client_order_id="buy-1")
        except ValueError as exc:
            outcomes["cancel_error"] = str(exc)

    threads = [threading.Thread(target=fill), threading.Thread(target=cancel)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert reconciliation_report()["passed"] is True
    state = snapshot()
    assert "cancel_error" not in outcomes
    assert outcomes["cancel"]["state"] == "cancelled"
    if "fill_error" in outcomes:
        assert outcomes["fill_error"] == "invalid_order_transition"
        assert state["fill_count"] == 0
        assert state["cash"] == "1000"
        assert state["positions"] == []
    else:
        assert outcomes["fill"]["filled_quantity"] == "4"
        assert state["fill_count"] == 1
        assert state["cash"] == "600"
        assert state["positions"][0]["quantity"] == "4"
    assert state["open_orders"] == []


def test_recovery_keeps_an_open_partial_order_actionable(db):
    _init()
    _working()
    _fill()
    resumed = recover()
    assert resumed["audit_ok"] is True
    assert resumed["open_orders"] == [{
        "client_order_id": "buy-1", "symbol": "BTC", "side": "BUY", "state": "partially_filled",
        "quantity": "10", "filled_quantity": "4", "leaves_quantity": "6", "price": "100",
    }]
    done = _fill(client_fill_id="fill-2", quantity="6")
    assert done["state"] == "filled"
    assert recover()["snapshot"]["cash"] == "0"
    assert recover()["open_orders"] == []
    assert reconcile()["passed"] is True


def test_kill_switch_blocks_a_fill_but_cancel_still_releases_the_order(db):
    _init()
    _working()
    set_kill_switch(True)
    with pytest.raises(ValueError, match="kill_switch_active"):
        _fill()
    blocked = _working(client_order_id="buy-2", quantity="1")
    assert blocked["reason"] == "kill_switch_active"
    assert blocked["fill"] is None
    cancelled = cancel_paper_order(client_order_id="buy-1")
    assert cancelled["state"] == "cancelled"
    assert snapshot()["cash"] == "1000"
    assert snapshot()["fill_count"] == 0
    assert snapshot()["open_orders"] == []
    assert reconcile()["passed"] is True


def test_stale_feed_blocks_a_fill_of_an_open_order(db):
    _init()
    _working()
    with pytest.raises(ValueError, match="stale_market_data"):
        _fill(observed_at=STALE)
    with pytest.raises(ValueError, match="stale_market_data"):
        _fill(client_fill_id="missing-time", observed_at=None)
    assert snapshot()["open_orders"][0]["state"] == "accepted"
    assert snapshot()["cash"] == "1000"
    assert cancel_paper_order(client_order_id="buy-1")["state"] == "cancelled"


def test_mark_to_market_unrealized_matches_the_report_and_a_tamper_is_audited(db):
    _init()
    _working()
    _fill()
    marked = mark_to_market({"BTC": "110"}, observed_at=FRESH, now=NOW)
    assert marked["unrealized_pnl"] == "40"
    assert marked["live_execution"] is False
    report = reconciliation_report()
    assert report["passed"] is True
    assert report["warnings"] == []
    unrealized = next(row for row in report["fields"] if row["field"] == "unrealized_pnl")
    assert unrealized == {"field": "unrealized_pnl", "expected": "40", "recorded": "40", "match": True}
    _fill(client_fill_id="fill-2", quantity="6")
    assert snapshot()["unrealized_pnl"] == "100"
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE paper_exec_meta SET unrealized_pnl='1' WHERE id=1")
    broken = reconciliation_report(record_mismatch=True)
    assert broken["passed"] is False
    assert "unrealized_pnl" in broken["breaks"]
    assert broken["mismatches"][0]["expected"] == "100"
    assert broken["mismatches"][0]["recorded"] == "1"
    again = reconciliation_report(record_mismatch=True)
    events = [row["event"] for row in snapshot()["audit"]]
    assert events.count("reconciliation_mismatch") == 1
    assert again["passed"] is False
    with pytest.raises(ValueError, match="reconciliation_break"):
        _working(client_order_id="buy-2", quantity="1")


def test_cash_tamper_is_flagged_with_expected_and_recorded(db):
    _init()
    _working(quantity="1")
    _fill(quantity="1")
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE paper_exec_meta SET cash='1' WHERE id=1")
    report = reconciliation_report(record_mismatch=True)
    assert report["passed"] is False
    cash = next(row for row in report["mismatches"] if row["field"] == "cash")
    assert cash["expected"] == "900"
    assert cash["recorded"] == "1"
    assert snapshot()["audit"][-1]["event"] == "reconciliation_mismatch"


def test_second_working_buy_cannot_spend_reserved_cash(db):
    _init()
    assert _working(quantity="8", price="100")["state"] == "accepted"
    rejected = _working(client_order_id="buy-2", quantity="3", price="100")
    assert rejected["status"] == "execution_rejected"
    assert rejected["reason"] == "insufficient_paper_cash"
    assert snapshot()["cash"] == "1000"
    assert len(snapshot()["open_orders"]) == 1


def test_live_execution_is_refused_for_fills_and_cancels(db):
    _init()
    _working()
    with pytest.raises(ValueError, match="live_execution_disabled"):
        _fill(live_execution=True)
    with pytest.raises(ValueError, match="live_execution_disabled"):
        cancel_paper_order(client_order_id="buy-1", live_execution=True)
    assert snapshot()["fill_count"] == 0
    assert snapshot()["open_orders"][0]["state"] == "accepted"


def test_legacy_ledger_migrates_and_still_reconciles(db):
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE paper_exec_meta (
            id INTEGER PRIMARY KEY CHECK(id=1),
            initial_cash TEXT NOT NULL, cash TEXT NOT NULL, realized_pnl TEXT NOT NULL,
            kill_switch INTEGER NOT NULL,
            max_order_notional TEXT NOT NULL, max_position_notional TEXT NOT NULL,
            max_daily_loss TEXT NOT NULL, max_gross_exposure TEXT NOT NULL,
            max_market_age_seconds TEXT NOT NULL, max_future_skew_seconds TEXT NOT NULL);
        CREATE TABLE paper_exec_positions (
            symbol TEXT PRIMARY KEY, quantity TEXT NOT NULL, average_cost TEXT NOT NULL);
        CREATE TABLE paper_exec_orders (
            client_order_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, side TEXT NOT NULL,
            quantity TEXT NOT NULL, price TEXT NOT NULL, fee TEXT NOT NULL,
            status TEXT NOT NULL, reason TEXT NOT NULL, limits_evaluated INTEGER NOT NULL,
            risk_decision_id TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE paper_exec_fills (
            seq INTEGER PRIMARY KEY, client_order_id TEXT NOT NULL UNIQUE,
            symbol TEXT NOT NULL, side TEXT NOT NULL, quantity TEXT NOT NULL,
            price TEXT NOT NULL, fee TEXT NOT NULL, realized_pnl TEXT NOT NULL);
        CREATE TABLE paper_exec_audit (
            seq INTEGER PRIMARY KEY, client_order_id TEXT, event TEXT NOT NULL,
            payload TEXT NOT NULL, prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL);
        """
    )
    payload = _canon_json({
        "cash": "1000", "live_execution": False, "broker_order_submitted": False,
    })
    digest = _row_hash("GENESIS", 1, "account_initialized", None, payload)
    conn.execute(
        """INSERT INTO paper_exec_meta VALUES (1,'1000','900','0',0,'10000','10000','1000','10000','300','60')"""
    )
    conn.execute("INSERT INTO paper_exec_positions VALUES ('BTC','1','100')")
    conn.execute(
        """INSERT INTO paper_exec_orders VALUES
           ('buy-1','BTC','BUY','1','100','0','filled','approved_for_simulation_only',1,'decision','')"""
    )
    conn.execute(
        "INSERT INTO paper_exec_fills VALUES (2,'buy-1','BTC','BUY','1','100','0','0')"
    )
    conn.execute(
        "INSERT INTO paper_exec_audit VALUES (1,NULL,'account_initialized',?,?,?)",
        (payload, "GENESIS", digest),
    )
    conn.commit()
    conn.close()
    resumed = recover()
    assert resumed["passed"] is True
    assert resumed["snapshot"]["cash"] == "900"
    assert resumed["snapshot"]["positions"][0]["quantity"] == "1"
    assert resumed["open_orders"] == []
    assert reconcile()["passed"] is True
    assert reconciliation_report()["journal_binding"] == "legacy_unbound"


def test_http_lifecycle_stays_paper_only(db):
    client = TestClient(app)
    opened = client.post("/paper/execution/initialize", json={"initial_cash": "1000"})
    assert opened.status_code == 200
    resting = client.post("/paper/execution/orders", json={
        "client_order_id": "http-1", "symbol": "BTC", "side": "BUY",
        "quantity": "4", "price": "100", "observed_at": FRESH, "now": NOW, "working": True,
    })
    assert resting.status_code == 200
    assert resting.json()["state"] == "accepted"
    assert resting.json()["broker_order_submitted"] is False
    live = client.post("/paper/execution/orders/http-1/fills", json={
        "client_fill_id": "http-fill", "quantity": "1", "observed_at": FRESH, "now": NOW,
        "live_execution": True,
    })
    assert live.status_code == 422
    partial = client.post("/paper/execution/orders/http-1/fills", json={
        "client_fill_id": "http-fill", "quantity": "1", "observed_at": FRESH, "now": NOW,
    })
    assert partial.status_code == 200
    assert partial.json()["state"] == "partially_filled"
    marked = client.post("/paper/execution/marks", json={
        "marks": {"BTC": "90"}, "observed_at": FRESH, "now": NOW,
    })
    assert marked.status_code == 200
    assert marked.json()["unrealized_pnl"] == "-10"
    report = client.get("/paper/execution/reconciliation-report")
    assert report.status_code == 200
    assert report.json()["passed"] is True
    assert report.json()["live_execution"] is False
    cancelled = client.post("/paper/execution/orders/http-1/cancel", json={})
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"
    replay = client.post("/paper/execution/orders/http-1/cancel", json={})
    assert replay.json()["status"] == "idempotent_replay"
    assert client.get("/paper/execution/reconcile").json()["passed"] is True
