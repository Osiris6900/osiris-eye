"""Surveillance over the canonical paper journal.

Caller-supplied event lists are not accepted. Indicators are review flags,
not findings of misconduct, and they do not enforce anything. A broken audit
or an unreconciled book returns unavailable rather than a clear review.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Any

from . import paper_execution as ledger

_SUBMIT_STATES = frozenset({"accepted", "partially_filled", "filled", "cancelled"})
_MIN_SUBMITS = 3
_CANCEL_RATIO = Decimal("0.8")
_BURST_WINDOW = timedelta(seconds=1)


def review_paper_journal() -> dict[str, Any]:
    """Read the paper audit and orders. No caller events and no thresholds."""
    report = {
        "state": "unavailable",
        "reason": "not_configured",
        "alerts": [],
        "orders_reviewed": 0,
        "fills_reviewed": 0,
        "finding_type": "review_indicators_not_misconduct_determinations",
        "human_review_required": True,
        "enforcement_action_taken": False,
        "live_execution": False,
        "source": "paper_journal",
    }
    try:
        path_ready = True
        ledger._database()
    except ValueError:
        path_ready = False
    if not path_ready:
        return report
    with ledger._lock:
        conn = ledger._connect()
        try:
            conn.execute("BEGIN")
            account = conn.execute("SELECT 1 FROM paper_exec_meta WHERE id=1").fetchone()
            if account is None:
                report["reason"] = "account_not_initialized"
                return report
            try:
                ledger._verify_audit(conn)
            except ValueError:
                report["reason"] = "audit_chain_corrupt"
                return report
            if ledger._reconcile_breaks(conn):
                report["reason"] = "reconciliation_break"
                return report
            orders = conn.execute(
                "SELECT client_order_id, symbol, state, created_at FROM paper_exec_orders"
            ).fetchall()
            fills = conn.execute(
                "SELECT symbol, side, quantity, price FROM paper_exec_fills"
            ).fetchall()
        finally:
            conn.close()
    counted = [row for row in orders if row["state"] in _SUBMIT_STATES]
    missing_time = [row for row in counted if ledger._parse_time(row["created_at"]) is None]
    if missing_time:
        report["reason"] = "timestamp_missing"
        report["orders_reviewed"] = len(counted)
        report["fills_reviewed"] = len(fills)
        return report
    alerts = _cancel_alerts(counted) + _burst_alerts(counted) + _round_trip_alerts(fills)
    report.update({
        "state": "reviewed",
        "reason": None,
        "alerts": alerts,
        "orders_reviewed": len(counted),
        "fills_reviewed": len(fills),
    })
    return report


def _cancel_alerts(orders) -> list[dict[str, Any]]:
    alerts = []
    by_symbol: dict[str, list] = defaultdict(list)
    for row in orders:
        by_symbol[row["symbol"]].append(row)
    for symbol, rows in sorted(by_symbol.items()):
        submits = len(rows)
        cancels = sum(row["state"] == "cancelled" for row in rows)
        if submits < _MIN_SUBMITS:
            continue
        ratio = Decimal(cancels) / Decimal(submits)
        if ratio >= _CANCEL_RATIO:
            alerts.append({
                "symbol": symbol,
                "indicator": "high_cancel_to_submit_ratio",
                "submits": submits,
                "cancels": cancels,
                "ratio": format(ratio, "f"),
            })
    return alerts


def _burst_alerts(orders) -> list[dict[str, Any]]:
    alerts = []
    by_symbol: dict[str, list] = defaultdict(list)
    for row in orders:
        by_symbol[row["symbol"]].append(ledger._parse_time(row["created_at"]))
    for symbol, times in sorted(by_symbol.items()):
        ordered = sorted(times)
        if len(ordered) >= _MIN_SUBMITS and any(
            ordered[index + 2] - ordered[index] <= _BURST_WINDOW
            for index in range(len(ordered) - 2)
        ):
            alerts.append({"symbol": symbol, "indicator": "rapid_submit_burst"})
    return alerts


def _round_trip_alerts(fills) -> list[dict[str, Any]]:
    alerts = []
    by_symbol: dict[str, list] = defaultdict(list)
    for row in fills:
        by_symbol[row["symbol"]].append(row)
    for symbol, rows in sorted(by_symbol.items()):
        buys = [(row["price"], row["quantity"]) for row in rows if row["side"] == "BUY"]
        sells = [(row["price"], row["quantity"]) for row in rows if row["side"] == "SELL"]
        if any(buy in sells for buy in buys):
            alerts.append({"symbol": symbol, "indicator": "same_price_round_trip"})
    return alerts
