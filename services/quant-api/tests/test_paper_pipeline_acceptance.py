"""Execute connected production modules, replacing only the external feed boundary."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
import sqlite3
import subprocess
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from osiris_api import paper_execution as ledger
from osiris_api.main import app
from osiris_api.paper_pipeline import run_paper_pipeline
from osiris_api.public_market_data import CoinbasePublicMarketDataAdapter
from osiris_api.risk import RiskLimits

NOW = datetime(2026, 11, 11, 1, 5, 10, tzinfo=timezone.utc)


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "execution.sqlite3"))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    ledger.initialize_paper_account("1000")


def bars(closes=(100, 101, 102, 110), now=NOW):
    start = now.replace(second=0, microsecond=0) - timedelta(minutes=len(closes))
    return [dict(symbol="BTC-USD", event_time=(start + timedelta(minutes=i)).isoformat(),
                 available_at=now.isoformat(), open=float(px), high=float(px), low=float(px),
                 close=float(px), volume=10.0) for i, px in enumerate(closes)]


def run(closes=(100, 101, 102, 110), now=NOW, **kwargs):
    with patch.object(CoinbasePublicMarketDataAdapter, "fetch_bars", return_value=bars(closes, now)):
        return run_paper_pipeline(**{"quantity": "2", "fee": "1", "now": now, **kwargs})


def test_buy_mark_partial_sell_flatten_exact_cash_fees_pnl_and_restart(account):
    buy = run()
    assert buy["strategy"]["side"] == "BUY"
    assert buy["workflow"]["risk"]["approved_for_paper"] is True
    assert buy["order"]["status"] == "filled"
    assert buy["account"]["cash"] == "779"
    assert buy["account"]["positions"] == [{"symbol": "BTC-USD", "quantity": "2", "average_cost": "110.5"}]
    assert buy["account"]["unrealized_pnl"] == "-1"
    # The sell signal comes from a NEW closed candle, with only known history.
    sale_time = NOW + timedelta(minutes=1)
    with patch.object(CoinbasePublicMarketDataAdapter, "fetch_bars", return_value=bars((140, 135, 130, 120), sale_time)):
        sell = run_paper_pipeline(quantity="1", fee="1", now=sale_time)
    assert sell["order"]["status"] == "filled"
    assert sell["account"]["cash"] == "898"
    assert sell["account"]["realized_pnl"] == "8.5"
    assert sell["account"]["unrealized_pnl"] == "9.5"
    equity = Decimal(sell["account"]["cash"]) + Decimal("120")
    assert equity == Decimal("1000") + Decimal("8.5") + Decimal("9.5")
    flat_time = NOW + timedelta(minutes=2)
    with patch.object(CoinbasePublicMarketDataAdapter, "fetch_bars", return_value=bars((130, 125, 120, 115), flat_time)):
        flat = run_paper_pipeline(quantity="1", fee="1", now=flat_time)
    assert flat["account"]["positions"] == []
    assert flat["account"]["cash"] == "1012"
    assert flat["account"]["realized_pnl"] == "12"
    assert flat["account"]["unrealized_pnl"] == "0"
    # New OS process: no shared locks, module globals, or open DB connection.
    output = subprocess.check_output([
        sys.executable, "-c", "import json; from osiris_api.paper_execution import recover; print(json.dumps(recover()))",
    ], env=os.environ.copy(), text=True)
    recovered = json.loads(output)
    assert recovered["passed"] is True
    assert recovered["snapshot"]["cash"] == "1012"
    assert recovered["snapshot"]["fill_count"] == 3
    assert flat["reconciliation"]["passed"] is True


def test_same_candle_concurrent_retries_never_duplicate_fill(account):
    with patch.object(CoinbasePublicMarketDataAdapter, "fetch_bars", return_value=bars()):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: run_paper_pipeline(quantity="2", fee="1", now=NOW), range(4)))
    assert all(r["reconciliation"]["passed"] for r in results)
    assert len({r["pipeline_run_id"] for r in results}) == 1
    assert ledger.snapshot()["fill_count"] == 1
    assert ledger.snapshot()["cash"] == "779"
    assert run()["replayed"] is True
    with pytest.raises(ValueError, match="pipeline_decision_conflict"):
        run(quantity="3")


def test_crash_after_order_commit_before_pipeline_receipt_recovers_once(account):
    with patch("osiris_api.paper_pipeline._account_result", side_effect=RuntimeError("process_crash")):
        with pytest.raises(RuntimeError, match="process_crash"):
            run()
    assert ledger.snapshot()["fill_count"] == 1
    replay = run()
    assert replay["order"]["status"] == "idempotent_replay"
    assert replay["replayed"] is True
    assert replay["account"]["fill_count"] == 1
    assert replay["reconciliation"]["passed"] is True


@pytest.mark.parametrize("kind", ["kill", "limit", "cash", "hold", "short"])
def test_blocks_leave_positions_cash_and_pnl_unchanged(tmp_path, monkeypatch, kind):
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "execution.sqlite3"))
    ledger.initialize_paper_account(
        "1000" if kind != "cash" else "100",
        RiskLimits(max_order_notional=100) if kind == "limit" else None,
    )
    if kind == "kill":
        ledger.set_kill_switch(True)
    before = ledger.snapshot()
    closes = (100, 100, 100, 100) if kind == "hold" else (110, 109, 105, 100) if kind == "short" else (100, 101, 102, 110)
    result = run(closes=closes)
    assert result["order"]["status"] in {"not_submitted", "execution_rejected"}
    after = ledger.snapshot()
    assert after["fill_count"] == 0
    assert after["cash"] == before["cash"]
    assert after["realized_pnl"] == after["unrealized_pnl"] == "0"
    assert after["positions"] == []
    assert result["reconciliation"]["passed"] is True


@pytest.mark.parametrize("kind", ["missing", "duplicate", "future_available", "gap", "nan", "wrong_symbol", "upstream"])
def test_bad_feed_fails_before_order_or_account_mutation(account, kind):
    records = bars()
    if kind == "missing":
        records.pop()
    elif kind == "duplicate":
        records[1] = records[0]
    elif kind == "future_available":
        records[-1]["available_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif kind == "gap":
        records[0]["event_time"] = (NOW - timedelta(minutes=4, seconds=30)).isoformat()
    elif kind == "nan":
        records[-1]["close"] = float("nan")
    elif kind == "wrong_symbol":
        records[-1]["symbol"] = "ETH-USD"
    with patch.object(CoinbasePublicMarketDataAdapter, "fetch_bars", return_value=records,
                      side_effect=RuntimeError("feed unavailable") if kind == "upstream" else None):
        with pytest.raises((ValueError, RuntimeError)):
            run_paper_pipeline(quantity="2", now=NOW)
    state = ledger.snapshot()
    assert state["order_count"] == state["fill_count"] == 0
    assert state["cash"] == "1000"


def test_stale_data_after_slow_fetch_blocks_before_order(account):
    with patch.object(CoinbasePublicMarketDataAdapter, "fetch_bars", return_value=bars()), patch(
        "osiris_api.paper_pipeline.datetime"
    ) as clock:
        clock.now.side_effect = [NOW, NOW + timedelta(seconds=301)]
        with pytest.raises(ValueError, match="stale_market_data"):
            run_paper_pipeline(quantity="2")
    assert ledger.snapshot()["fill_count"] == 0


def test_reconciliation_corruption_blocks_even_a_saved_replay(account):
    run()
    with sqlite3.connect(os.environ["OSIRIS_PAPER_EXECUTION_DB"]) as conn:
        conn.execute("UPDATE paper_exec_meta SET cash='999' WHERE id=1")
    with pytest.raises(ValueError, match="reconciliation"):
        run()


def test_http_contract_uses_auth_and_forbids_caller_prices_risk_clock(account, monkeypatch):
    monkeypatch.setenv("OSIRIS_API_TOKEN", "test-pipeline-token")
    client = TestClient(app)
    assert client.post("/paper/pipeline/run", json={}).status_code == 401
    headers = {"Authorization": "Bearer test-pipeline-token"}
    for field, value in [("price", "1"), ("now", NOW.isoformat()), ("risk_approved", True), ("signals", [1, 1])]:
        assert client.post("/paper/pipeline/run", headers=headers, json={field: value}).status_code == 422
    with patch("osiris_api.paper_pipeline.run_paper_pipeline", side_effect=ValueError("stale_market_data")):
        result = client.post("/paper/pipeline/run", headers=headers, json={})
    assert result.status_code == 422
    assert result.json()["detail"] == "stale_market_data"


def test_delayed_latest_candle_uses_fresh_contiguous_prior_window(account):
    delayed = NOW - timedelta(minutes=1)
    with patch.object(CoinbasePublicMarketDataAdapter, "fetch_bars", return_value=bars(now=delayed)):
        result = run_paper_pipeline(quantity="2", now=NOW)
    assert result["market"]["observed_at"] == delayed.replace(second=0, microsecond=0).isoformat()
    assert result["order"]["status"] == "filled"
    assert result["reconciliation"]["passed"] is True


def test_http_success_uses_same_ledger_for_fill_and_status(account, monkeypatch):
    monkeypatch.setenv("OSIRIS_API_TOKEN", "pipeline-test")
    current = datetime.now(timezone.utc)
    client = TestClient(app)
    headers = {"Authorization": "Bearer pipeline-test"}
    with patch.object(CoinbasePublicMarketDataAdapter, "fetch_bars", return_value=bars(now=current)):
        response = client.post("/paper/pipeline/run", headers=headers, json={"quantity": "2", "fee": "1"})
        assert response.status_code == 200, response.text
        filled = response.json()
        assert filled["order"]["status"] == "filled"
        status = client.get("/paper/pipeline/status", headers=headers).json()
        assert status["order"] == "filled"
        assert status["cash"] == filled["account"]["cash"] == "779"
        assert status["positions"] == filled["account"]["positions"]
        assert status["pnl"] == {"realized": "0", "unrealized": "-1", "equity": "999"}
        ledger.set_kill_switch(True)
        replay = client.post("/paper/pipeline/run", headers=headers, json={"quantity": "2", "fee": "1"})
    assert replay.json()["replayed"] is True
    assert replay.json()["account"]["fill_count"] == 1
    assert client.get("/paper/pipeline/status", headers=headers).json()["risk"] == "halted"


def test_concurrent_control_change_does_not_hide_a_fill_behind_a_blocked_receipt(account):
    """One worker's rejected proposal cannot mask another worker's committed fill."""
    from copy import deepcopy
    from threading import Barrier, Event, Lock

    from osiris_api import paper_pipeline as pipeline
    from osiris_api.paper_pipeline_status import get_paper_pipeline_status, record_pipeline_run

    real_context = ledger.execution_context
    real_decision = pipeline._decision
    real_submit = ledger.submit_approved_workflow
    context_lock, both_claimed, blocked_published = Lock(), Barrier(2), Event()
    contexts = 0
    blocked_receipts = []

    def read_changing_controls(**kwargs):
        nonlocal contexts
        with context_lock:
            # These are real persisted controls, read into two separate snapshots.
            ledger.set_kill_switch(contexts == 0)
            contexts += 1
            return real_context(**kwargs)

    def claim_together(run_id, fingerprint, result=None):
        saved = real_decision(run_id, fingerprint, result)
        if result is None:
            both_claimed.wait(timeout=5)
        return saved

    def submit_after_blocked_receipt(workflow, **kwargs):
        if workflow["risk"]["approved_for_paper"]:
            assert blocked_published.wait(timeout=5)
        return real_submit(workflow, **kwargs)

    def retain_first_receipt(result):
        record_pipeline_run(result)
        if result["order"]["status"] == "not_submitted":
            blocked_receipts.append(deepcopy(result))
            blocked_published.set()

    with patch.object(CoinbasePublicMarketDataAdapter, "fetch_bars", return_value=bars()), patch.object(
        ledger, "execution_context", read_changing_controls,
    ), patch.object(pipeline, "_decision", claim_together), patch.object(
        ledger, "submit_approved_workflow", submit_after_blocked_receipt,
    ), patch.object(pipeline, "record_pipeline_run", retain_first_receipt):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda _: run_paper_pipeline(quantity="2", fee="1", now=NOW), range(2),
            ))

    assert len(blocked_receipts) == 1
    assert ledger.snapshot()["fill_count"] == 1
    filled = [result for result in results if result["order"].get("state") == "filled"]
    assert len(filled) == 1
    assert filled[0]["account"]["cash"] == "779"
    # A delayed status writer or restart may restore the earlier receipt. Current
    # OMS state still owns the order view, even though that proposal was blocked.
    record_pipeline_run(blocked_receipts[0])
    assert get_paper_pipeline_status()["order"] == "filled"
    replay = run()
    assert replay["replayed"] is True
    assert replay["order"]["state"] == "filled"
    assert replay["account"]["fill_count"] == 1
