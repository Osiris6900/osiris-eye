"""Admin and pipeline views must report one reconciled paper execution account."""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from osiris_api import admin_centre, durable_paper_ledger, paper_pipeline_status
from osiris_api.main import app
from osiris_api.paper_execution import (
    apply_paper_fill,
    cancel_paper_order,
    execution_status,
    initialize_paper_account,
    mark_to_market,
    set_kill_switch,
    submit_paper_order,
)


@pytest.fixture
def account(tmp_path, monkeypatch):
    path = tmp_path / "canonical.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(path))
    monkeypatch.setenv("OSIRIS_PAPER_LEDGER_DB", str(tmp_path / "legacy.sqlite3"))
    monkeypatch.setenv("OSIRIS_ADMIN_TOKENS", json.dumps({"analyst": "fixture-analyst"}))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    monkeypatch.delenv("OSIRIS_API_TOKEN", raising=False)
    monkeypatch.setattr(paper_pipeline_status, "_latest", None)
    initialize_paper_account("1000")
    return path


def order(key="buy", **extra):
    stamp = datetime.now(UTC).isoformat()
    return submit_paper_order(**{
        "client_order_id": key, "symbol": "BTC-USD", "side": "BUY", "quantity": "2",
        "price": "100", "fee": "2", "observed_at": stamp, "now": stamp, **extra,
    })


def record_run(submitted):
    stamp = datetime.now(UTC).isoformat()
    paper_pipeline_status.record_pipeline_run({
        "symbol": "BTC-USD", "provider": "fixture", "decision_time": stamp,
        "pipeline_run_id": "fixture-run",
        "market": {"observed_at": stamp, "max_age_seconds": 300},
        "workflow": {"risk": {"approved_for_paper": True}}, "order": submitted,
    })


def test_canonical_account_replaces_a_divergent_legacy_view_without_merging_it(account):
    durable_paper_ledger.initialize_account("9999")
    durable_paper_ledger.synthetic_fill("legacy-fill", "OTHER", "BUY", "1", "10")
    record_run(order())
    stamp = datetime.now(UTC).isoformat()
    mark_to_market({"BTC-USD": "110"}, observed_at=stamp, now=stamp)
    admin = admin_centre.paper_status()
    pipeline = paper_pipeline_status.get_paper_pipeline_status()
    assert admin["source_ledger"] == "canonical_execution"
    assert admin["cash"] == pipeline["cash"] == "798"
    assert admin["positions"] == pipeline["positions"]
    assert admin["pnl"] == pipeline["pnl"] == {"realized": "0", "unrealized": "18", "equity": "1018"}
    assert admin["market_value"] == "220"
    assert admin["recent_fills"][0]["fee"] == "2"
    assert admin["recent_fills"][0]["created_at"] is None
    assert admin["reconciliation"]["passed"] is True
    assert durable_paper_ledger.snapshot()["cash"] == "9989"
    assert admin["live_trading_authorized"] is False


def test_partial_fills_open_orders_cancellation_and_later_sales_match_both_views(account):
    record_run(order(working=True))
    now = datetime.now(UTC).isoformat()
    apply_paper_fill(client_order_id="buy", client_fill_id="part", quantity="1",
                     observed_at=now, now=now)
    admin = admin_centre.paper_status()
    assert admin["cash"] == "899"
    assert admin["open_orders"][0]["state"] == "partially_filled"
    assert admin["orders"][0]["filled_quantity"] == "1"
    cancel_paper_order(client_order_id="buy")
    order("sell", side="SELL", quantity="1", price="110", fee="1")
    admin = admin_centre.paper_status()
    pipeline = paper_pipeline_status.get_paper_pipeline_status()
    assert admin["cash"] == pipeline["cash"] == "1008"
    assert admin["pnl"] == pipeline["pnl"] == {"realized": "8", "unrealized": "0", "equity": "1008"}
    assert admin["open_orders"] == []
    assert {row["state"] for row in admin["orders"]} == {"cancelled", "filled"}
    assert admin["fill_count"] == pipeline["fill_count"] == 2


@pytest.mark.parametrize("mark_age", [None, 301, -61])
def test_missing_stale_or_future_marks_preserve_known_cash_and_hide_partial_values(account, mark_age):
    record_run(order())
    if mark_age is not None:
        stamp = (datetime.now(UTC) - timedelta(seconds=mark_age)).isoformat()
        mark_to_market({"BTC-USD": "110"}, observed_at=stamp, now=stamp)
    admin = admin_centre.paper_status()
    pipeline = paper_pipeline_status.get_paper_pipeline_status()
    assert admin["cash"] == pipeline["cash"] == "798"
    assert admin["pnl"] == pipeline["pnl"] == {"realized": "0", "unrealized": None, "equity": None}
    assert admin["market_value"] is None
    assert admin["marks_available"] is False
    assert admin["valuation_warnings"] == pipeline["valuation_warnings"]


@pytest.mark.parametrize("corruption", ["cash", "audit"])
def test_failed_reconciliation_hides_unverified_accounting_and_raises_a_critical_flag(account, corruption):
    record_run(order())
    with sqlite3.connect(account) as db:
        db.execute("UPDATE paper_exec_meta SET cash='999999'" if corruption == "cash" else
                   "UPDATE paper_exec_audit SET payload='{}' WHERE seq=1")
    admin = admin_centre.paper_status()
    assert admin["initialized"] is True
    assert admin["reconciliation"]["passed"] is False
    assert all(admin[field] is None for field in ("cash", "positions", "pnl", "orders", "recent_fills"))
    assert paper_pipeline_status.get_paper_pipeline_status()["cash"] is None
    risk = admin_centre.risk_status(paper=admin)
    assert any(flag["kind"] == "paper_reconciliation_failed" and flag["severity"] == "critical"
               for flag in risk["flags"])


def test_kill_switch_is_shared_with_the_admin_risk_view(account):
    set_kill_switch(True)
    admin = admin_centre.paper_status()
    risk = admin_centre.risk_status(paper=admin)
    assert admin["kill_switch_active"] is True
    assert any(flag["kind"] == "paper_execution_halted" for flag in risk["flags"])


def test_missing_canonical_database_never_falls_back_or_creates_files(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_LEDGER_DB", str(tmp_path / "legacy.sqlite3"))
    durable_paper_ledger.initialize_account("9999")
    missing = tmp_path / "absent" / "execution.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(missing))
    admin = admin_centre.paper_status()
    assert admin["source_ledger"] == "canonical_execution"
    assert admin["initialized"] is False
    assert admin["cash"] is None
    assert not missing.parent.exists()


def test_admin_read_does_not_modify_the_execution_database(account):
    order()
    before = account.read_bytes()
    assert admin_centre.paper_status()["cash"] == "798"
    assert execution_status(read_only=True)["reconciliation"]["passed"] is True
    assert account.read_bytes() == before


def test_new_process_reads_the_same_canonical_account_and_pnl(account):
    order()
    process = subprocess.run(
        [sys.executable, "-c", ("import json; from osiris_api.admin_centre import paper_status; "
                               "print(json.dumps(paper_status()))")],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        check=True, text=True, capture_output=True, timeout=20,
    )
    restarted = json.loads(process.stdout)
    assert restarted["cash"] == "798"
    assert restarted["fill_count"] == 1
    assert restarted["pnl"]["realized"] == "0"
    assert restarted["reconciliation"]["passed"] is True


def test_authenticated_admin_http_account_matches_the_execution_status(account):
    order()
    with TestClient(app) as client:
        assert client.get("/admin/paper/status").status_code == 401
        response = client.get("/admin/paper/status", headers={"Authorization": "Bearer fixture-analyst"})
    assert response.status_code == 200
    body = response.json()
    canonical = execution_status()["account"]
    assert body["cash"] == canonical["cash"]
    assert body["positions"] == canonical["positions"]
    assert body["orders"] == canonical["orders"]
    assert Decimal(body["cost_basis_gross"]) == 202
    assert "fixture-analyst" not in response.text
