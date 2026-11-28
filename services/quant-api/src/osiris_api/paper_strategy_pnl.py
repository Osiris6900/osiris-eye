"""Realized P&L of the paper book grouped by the order's strategy tag.

The tag is the one stored on the order decision. A fill with no tag is
``untagged``. Realized P&L stays on the fill that booked it, which is the
closing order. Unrealized P&L is not split, because one position can mix tags.
"""
from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from typing import Any

from . import paper_execution as ledger


def paper_strategy_pnl() -> dict[str, Any]:
    report: dict[str, Any] = {
        "state": "unavailable",
        "reason": "not_configured",
        "strategies": [],
        "realized_pnl": None,
        "fees": None,
        "unrealized_pnl_attributed": False,
        "realized_pnl_follows_closing_order": True,
        "strategy_budgets_enforced": False,
        "book_mutated": False,
        "live_execution": False,
        "source": "paper_book",
    }
    try:
        ledger._database()
    except ValueError:
        return report
    with ledger._lock:
        conn = ledger._connect()
        try:
            conn.execute("BEGIN")
            account = conn.execute("SELECT * FROM paper_exec_meta WHERE id=1").fetchone()
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
            orders = {
                row["client_order_id"]: row
                for row in conn.execute("SELECT * FROM paper_exec_orders")
            }
            grouped: dict[str | None, dict[str, Fraction | int]] = {}
            for fill in conn.execute(
                """SELECT client_order_id, fee, realized_pnl
                   FROM paper_exec_fills ORDER BY seq"""
            ):
                order = orders.get(fill["client_order_id"])
                if order is None:
                    report["reason"] = "reconciliation_break"
                    return report
                tag = ledger._order_strategy(conn, order)
                bucket = grouped.setdefault(tag, {
                    "realized": Fraction(0), "fees": Fraction(0), "fills": 0,
                })
                bucket["realized"] += Fraction(Decimal(fill["realized_pnl"]))
                bucket["fees"] += Fraction(Decimal(fill["fee"]))
                bucket["fills"] = int(bucket["fills"]) + 1
        finally:
            conn.close()
    realized = sum((bucket["realized"] for bucket in grouped.values()), Fraction(0))
    fees = sum((bucket["fees"] for bucket in grouped.values()), Fraction(0))
    if ledger._finite_decimal(realized) != Decimal(account["realized_pnl"]):
        report["reason"] = "reconciliation_break"
        return report
    rows = [
        {
            "strategy_id": tag,
            "realized_pnl": _money(bucket["realized"]),
            "fees": _money(bucket["fees"]),
            "fills": bucket["fills"],
        }
        for tag, bucket in grouped.items()
    ]
    rows.sort(key=lambda row: (row["strategy_id"] is None, row["strategy_id"] or ""))
    report.update({
        "state": "ready",
        "reason": None,
        "strategies": rows,
        "realized_pnl": _money(realized),
        "fees": _money(fees),
    })
    return report


def _money(value: Fraction | int) -> str:
    return ledger._canonical(ledger._finite_decimal(value if isinstance(value, Fraction) else Fraction(value)))
