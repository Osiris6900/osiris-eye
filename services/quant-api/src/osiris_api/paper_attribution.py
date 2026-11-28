"""Symbol P&L attribution of the canonical paper book.

Realized P&L, fees and unrealized price movement are read from the reconciled
ledger. Their sum is the price movement. Strategy tags and arrival slippage
live on the control-plane attribution report; this book has neither, so those
fields are not invented from caller input.
"""
from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from typing import Any

from . import paper_execution as ledger


def paper_book_attribution(as_of: object) -> dict[str, Any]:
    when = ledger._parse_time(as_of)
    if when is None or not isinstance(as_of, str):
        raise ValueError("invalid_attribution_time")
    report: dict[str, Any] = {
        "state": "unavailable",
        "reason": "not_configured",
        "symbols": [],
        "realized_pnl": None,
        "fees": None,
        "unrealized_pnl": None,
        "price_movement": None,
        "strategy_attributed": False,
        "slippage_attributed": False,
        "reconciled_to_account": False,
        "book_mutated": False,
        "live_execution": False,
        "source": "paper_book",
        "identity": "price_movement = realized_pnl + unrealized_pnl + fees",
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
            exact = ledger._closing_settlement_enabled(conn)
            fills = conn.execute(
                """SELECT symbol, fee, realized_pnl
                   FROM paper_exec_fills ORDER BY seq"""
            ).fetchall()
            positions = {
                row["symbol"]: (Decimal(row["quantity"]), Decimal(row["average_cost"]))
                for row in conn.execute(
                    "SELECT symbol, quantity, average_cost FROM paper_exec_positions"
                )
            }
            marks = {
                row["symbol"]: row
                for row in conn.execute(
                    "SELECT symbol, price, observed_at FROM paper_exec_marks"
                )
            }
        finally:
            conn.close()
    realized_by: dict[str, Fraction] = {}
    fees_by: dict[str, Fraction] = {}
    for fill in fills:
        symbol = fill["symbol"]
        realized_by[symbol] = realized_by.get(symbol, Fraction(0)) + Fraction(Decimal(fill["realized_pnl"]))
        fees_by[symbol] = fees_by.get(symbol, Fraction(0)) + Fraction(Decimal(fill["fee"]))
    symbols = sorted(set(realized_by) | set(positions))
    rows = []
    realized_total = fees_total = unrealized_total = Fraction(0)
    for symbol in symbols:
        quantity, average = positions.get(symbol, (Decimal("0"), Decimal("0")))
        if quantity != 0:
            mark = marks.get(symbol)
            if mark is None or not ledger._fresh(account, mark["observed_at"], as_of):
                report.update(state="incomplete", reason="mark_required")
                return report
            if exact:
                unrealized = Fraction(quantity) * (
                    Fraction(Decimal(mark["price"])) - Fraction(average)
                )
            else:
                unrealized = Fraction(quantity * (Decimal(mark["price"]) - average))
        else:
            unrealized = Fraction(0)
        realized = realized_by.get(symbol, Fraction(0))
        fees = fees_by.get(symbol, Fraction(0))
        movement = realized + unrealized + fees
        realized_total += realized
        fees_total += fees
        unrealized_total += unrealized
        rows.append({
            "symbol": symbol,
            "realized_pnl": _money(realized),
            "fees": _money(fees),
            "unrealized_pnl": _money(unrealized),
            "price_movement": _money(movement),
        })
    if not _matches(realized_total, account["realized_pnl"]) or not _matches(
        unrealized_total, account["unrealized_pnl"],
    ):
        report["reason"] = "reconciliation_break"
        return report
    report.update({
        "state": "ready",
        "reason": None,
        "symbols": rows,
        "realized_pnl": _money(realized_total),
        "fees": _money(fees_total),
        "unrealized_pnl": _money(unrealized_total),
        "price_movement": _money(realized_total + unrealized_total + fees_total),
        "reconciled_to_account": True,
    })
    return report


def _money(value: Fraction) -> str:
    return ledger._canonical(ledger._finite_decimal(value))


def _matches(value: Fraction, recorded: str) -> bool:
    return ledger._finite_decimal(value) == Decimal(recorded)
