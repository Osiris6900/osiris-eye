"""Read-only paper pipeline status and authoritative execution-ledger accounting.

Evaluation-only helpers retain their in-process view. Completed pipeline runs
are durable; their account balances are refreshed from the execution ledger.
"""
import json
import math
import os
import sqlite3
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from fastapi import APIRouter

router = APIRouter()
_lock = Lock()
_latest = None


def _validate_run(result: object) -> None:
    if not isinstance(result, dict):
        raise ValueError("paper_pipeline_status_invalid")
    if any(not isinstance(result.get(field), dict) for field in ("workflow", "market", "order")):
        raise ValueError("paper_pipeline_status_invalid")
    if not isinstance(result["workflow"].get("risk"), dict):
        raise ValueError("paper_pipeline_status_invalid")
    if any(not isinstance(result.get(field), str)
           for field in ("symbol", "provider", "decision_time")):
        raise ValueError("paper_pipeline_status_invalid")
    try:
        max_age = float(result["market"].get("max_age_seconds", 300))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("paper_pipeline_status_invalid") from exc
    if not math.isfinite(max_age) or max_age <= 0:
        raise ValueError("paper_pipeline_status_invalid")


def record_pipeline_run(result: dict) -> None:
    """Keep the latest completed run beside the durable paper execution ledger.

    This record describes a historical decision, not an authorization to replay
    its order. The order engine's idempotency and current controls still apply.
    """
    path = os.getenv("OSIRIS_PAPER_EXECUTION_DB")
    if not path or path == ":memory:":
        raise ValueError("paper_execution_db_not_configured")
    _validate_run(result)
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with _lock, sqlite3.connect(path, timeout=15) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS paper_pipeline_runs (
            id INTEGER PRIMARY KEY CHECK(id=1), result TEXT NOT NULL)""")
        conn.execute(
            "INSERT INTO paper_pipeline_runs(id,result) VALUES (1,?) "
            "ON CONFLICT(id) DO UPDATE SET result=excluded.result", (encoded,),
        )


def _read_pipeline_run() -> dict | None:
    path = os.getenv("OSIRIS_PAPER_EXECUTION_DB")
    if not path or path == ":memory:" or not Path(path).is_file():
        return None
    # Reading status must not create an account or a new database.
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True,
                         timeout=15) as conn:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_pipeline_runs'"
        ).fetchone() is None:
            return None
        row = conn.execute("SELECT result FROM paper_pipeline_runs WHERE id=1").fetchone()
    if row is None:
        return None
    result = json.loads(row[0])
    _validate_run(result)
    return result


def _run_status(result: dict) -> dict:
    workflow = result.get("workflow", {})
    risk = workflow.get("risk", {})
    order = result.get("order", {})
    approved = risk.get("approved_for_paper") is True
    order_state = order.get("state", order.get("original_status", order.get("status")))
    order_view = (
        "filled" if order_state == "filled" else
        "pending" if order_state in {"accepted", "partially_filled"} else "none"
    )
    blocked = order_state in {"rejected", "risk_rejected", "execution_rejected"}
    market = result.get("market", {})
    feed = "missing"
    try:
        observed = datetime.fromisoformat(market["observed_at"].replace("Z", "+00:00"))
        if observed.tzinfo is not None:
            age = (datetime.now(timezone.utc) - observed).total_seconds()
            feed = "valid" if -60 <= age <= float(market.get("max_age_seconds", 300)) else "stale"
    except (KeyError, TypeError, ValueError):
        pass
    return {
        "mode": "offline-paper", "live_execution": False, "broker_order_submitted": False,
        "feed": feed, "proposal": "approved" if approved else "rejected",
        "risk": "pass" if approved and not blocked else "blocked", "order": order_view,
        "instrument": result.get("symbol", ""), "timestamp": result.get("decision_time", ""),
        "source": result.get("provider", "public-market-data"),
        "detail": "Public market data; simulated paper fills. No broker orders.",
        "intent": deepcopy(workflow.get("paper_order_intent")),
        "provenance": deepcopy(market), "paper_intent_id": result.get("pipeline_run_id"),
        "order_detail": deepcopy(order), "strategy": deepcopy(result.get("strategy")),
        "positions": None, "cash": None, "pnl": None,
        "reconciliation": None,
    }


def _refresh_account(status: dict) -> dict:
    # Import lazily: the execution engine can record a pipeline run without a
    # module-import cycle. Evaluation-only status never reads the legacy ledger.
    from .paper_execution import account_valuation, execution_status

    current = execution_status(read_only=True)
    account, report = current["account"], current["reconciliation"]
    status["reconciliation"] = report
    status["account_initialized"] = account.get("initialized") is True
    status["fill_count"] = account.get("fill_count", 0)
    if account.get("kill_switch_active") is True:
        status["risk"] = "halted"
    if report.get("passed") is not True:
        status.update({
            "risk": "blocked", "order": "none", "positions": None, "cash": None, "pnl": None,
            "detail": "Paper ledger reconciliation failed; accounting unavailable.",
        })
        return status
    status["cash"] = account["cash"]
    status["positions"] = account["positions"]
    status.update(account_valuation(
        account, max_age_seconds=float(status.get("provenance", {}).get("max_age_seconds", 300)),
    ))
    # A previously pending order may have filled or been cancelled since this run.
    key = status.get("order_detail", {}).get("client_order_id")
    if key:
        latest_order = next((order for order in account.get("orders", [])
                             if order["client_order_id"] == key), None)
        if latest_order is not None:
            state = latest_order["state"]
            status["order_detail"].update(latest_order)
            status["order"] = (
                "filled" if state == "filled" else
                "pending" if state in {"accepted", "partially_filled"} else "none"
            )
            if state in {"accepted", "partially_filled", "filled"}:
                # Concurrent proposals can observe different kill-switch states.
                # Display the committed OMS approval; retained workflow evidence
                # still records the historical proposal's own evaluation.
                status["proposal"] = "approved"
                if account.get("kill_switch_active") is not True:
                    status["risk"] = "pass"
        else:
            status.update({"order": "none", "risk": "blocked",
                           "detail": "Recorded paper order is missing from the execution ledger."})
    return status


def record_paper_workflow(result: dict) -> None:
    global _latest
    risk = result.get("risk", {})
    approved = risk.get("approved_for_paper") is True
    snapshot = {
        "mode": "offline-paper",
        "feed": "valid" if result.get("observation_provenance") else "missing",
        "proposal": "approved" if approved else "rejected",
        "risk": "pass" if approved else "blocked",
        "order": "none",
        "instrument": str(result.get("symbol", "")),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "verified-stored-observation" if result.get("observation_provenance") else "in-process-paper-workflow",
        "detail": "Paper evaluation only; no broker orders or fills.",
        "intent": dict(result.get("paper_order_intent", {})),
        "provenance": deepcopy(result.get("observation_provenance")),
        "paper_intent_id": result.get("paper_intent_id"),
        "positions": None,
        "cash": None,
        "pnl": None,
    }
    with _lock:
        _latest = snapshot


@router.get("/paper/pipeline/status")
def get_paper_pipeline_status():
    with _lock:
        fallback = deepcopy(_latest)
        try:
            result = _read_pipeline_run()
        except (ValueError, ArithmeticError, sqlite3.Error):
            result = None
            fallback = {
                "mode": "offline-paper", "feed": "missing", "proposal": "none",
                "risk": "blocked", "order": "none", "instrument": "", "timestamp": "",
                "source": "unavailable", "detail": "Durable paper pipeline status unavailable.",
                "intent": None, "provenance": None, "positions": None, "cash": None, "pnl": None,
            }
    if result is not None:
        try:
            return _refresh_account(_run_status(result))
        except (ValueError, ArithmeticError, sqlite3.Error):
            status = _run_status(result)
            status.update({"risk": "blocked", "order": "none",
                           "detail": "Paper execution ledger unavailable; accounting unavailable."})
            return status
    if fallback is None:
        return {
            "mode": "offline-paper",
            "feed": "missing",
            "proposal": "none",
            "risk": "blocked",
            "order": "none",
            "instrument": "",
            "timestamp": "",
            "source": "unavailable",
            "detail": "No paper workflow evaluation has run in this process.",
            "intent": None,
            "provenance": None,
            "paper_intent_id": None,
            "positions": None,
            "cash": None,
            "pnl": None,
        }
    return fallback
