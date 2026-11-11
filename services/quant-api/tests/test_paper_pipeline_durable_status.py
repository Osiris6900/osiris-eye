"""Dashboard accounting follows the execution ledger across restart and updates."""
from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from osiris_api import paper_pipeline_status as status
from osiris_api.paper_execution import (
    cancel_paper_order, initialize_paper_account, mark_to_market,
    set_kill_switch, submit_paper_order,
)


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "paper.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(path))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    initialize_paper_account("1000")
    return path


def _order(key="buy", **overrides):
    now = datetime.now(timezone.utc).isoformat()
    args = dict(client_order_id=key, symbol="BTC-USD", side="BUY", quantity="1",
                price="100", fee="1", observed_at=now, now=now)
    args.update(overrides)
    return submit_paper_order(**args)


def _record(order):
    now = datetime.now(timezone.utc).isoformat()
    result = {
        "symbol": "BTC-USD", "provider": "coinbase", "decision_time": now,
        "pipeline_run_id": "run-1",
        "market": {"event_time": now, "observed_at": now, "price": "100",
                   "bars_sha256": "abc", "max_age_seconds": 300},
        "workflow": {"risk": {"approved_for_paper": True},
                     "paper_order_intent": {"symbol": "BTC-USD", "side": "BUY",
                                            "quantity": 1, "status": "PAPER_INTENT_APPROVED"}},
        "order": order,
        # A run's historical snapshot is never used for current accounting.
        "account": {"cash": "999999999"}, "reconciliation": {"passed": True},
    }
    status.record_pipeline_run(result)


def _mark(price="110"):
    now = datetime.now(timezone.utc).isoformat()
    mark_to_market({"BTC-USD": price}, observed_at=now, now=now)


def test_status_survives_new_process_and_uses_current_execution_balances(ledger):
    _record(_order())
    _mark()
    body = status.get_paper_pipeline_status()
    assert body["feed"] == "valid"
    assert body["order"] == "filled"
    assert body["cash"] == "899"
    assert body["positions"] == [{"symbol": "BTC-USD", "quantity": "1", "average_cost": "101"}]
    assert body["pnl"] == {"realized": "0", "unrealized": "9", "equity": "1009"}
    assert body["reconciliation"]["passed"] is True

    # A later order updates the ledger without updating the pipeline receipt.
    _order("sell", side="SELL", quantity="0.5", price="120", fee="1")
    fresh_process = subprocess.run(
        [sys.executable, "-c", "import json; from osiris_api.paper_pipeline_status import "
         "get_paper_pipeline_status; print(json.dumps(get_paper_pipeline_status()))"],
        check=True, text=True, capture_output=True, env=os.environ.copy(),
    )
    restarted = json.loads(fresh_process.stdout)
    assert restarted["paper_intent_id"] == "run-1"
    assert restarted["cash"] == "958"
    assert restarted["positions"][0]["quantity"] == "0.5"
    assert restarted["pnl"] == {"realized": "8.5", "unrealized": "4.5", "equity": "1013"}
    assert restarted["fill_count"] == 2


def test_pending_order_status_tracks_cancellation_and_kill_switch(ledger):
    _record(_order(working=True))
    assert status.get_paper_pipeline_status()["order"] == "pending"
    cancel_paper_order(client_order_id="buy")
    body = status.get_paper_pipeline_status()
    assert body["order"] == "none"
    assert body["order_detail"]["state"] == "cancelled"
    assert body["cash"] == "1000"
    set_kill_switch(True)
    assert status.get_paper_pipeline_status()["risk"] == "halted"


def test_missing_marks_do_not_display_zero_unrealized_or_equity(ledger):
    _record(_order())
    body = status.get_paper_pipeline_status()
    assert body["cash"] == "899"
    assert body["pnl"] == {"realized": "0", "unrealized": None, "equity": None}


def test_stale_marks_hide_unrealized_and_equity_but_keep_cash_and_realized(ledger):
    _record(_order())
    old = (datetime.now(timezone.utc) - timedelta(seconds=301)).isoformat()
    mark_to_market({"BTC-USD": "110"}, observed_at=old, now=old)
    body = status.get_paper_pipeline_status()
    assert body["cash"] == "899"
    assert body["pnl"] == {"realized": "0", "unrealized": None, "equity": None}
    assert body["valuation_warnings"] == ["stale_mark:BTC-USD"]
    assert body["valuation_as_of"] is None


def test_reconciliation_failure_hides_unverified_accounting(ledger):
    _record(_order())
    with sqlite3.connect(ledger) as conn:
        conn.execute("UPDATE paper_exec_meta SET cash='999999' WHERE id=1")
    body = status.get_paper_pipeline_status()
    assert body["reconciliation"]["passed"] is False
    assert body["risk"] == "blocked"
    assert body["cash"] is None
    assert body["positions"] is None
    assert body["pnl"] is None


def test_reading_unconfigured_status_does_not_create_a_database(tmp_path, monkeypatch):
    path = tmp_path / "missing.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(path))
    monkeypatch.setattr(status, "_latest", None)
    body = status.get_paper_pipeline_status()
    assert body["cash"] is None
    assert body["order"] == "none"
    assert not path.exists()


def test_corrupt_durable_status_fails_closed(ledger):
    _record(_order())
    with sqlite3.connect(ledger) as conn:
        conn.execute("UPDATE paper_pipeline_runs SET result='[]'")
    body = status.get_paper_pipeline_status()
    assert body["risk"] == "blocked"
    assert body["cash"] is None
