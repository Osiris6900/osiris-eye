"""Stress the canonical paper book. Positions come from the book.

The caller supplies a price shock, not a replacement portfolio. The book is
not written. A stale or missing mark fails closed instead of inventing a P&L.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any

from . import paper_execution as ledger


def stress_paper_book(as_of: object, shocks: object) -> dict[str, Any]:
    when = ledger._parse_time(as_of)
    if when is None or not isinstance(as_of, str):
        raise ValueError("invalid_stress_time")
    parsed = _shocks(shocks)
    report = {
        "state": "unavailable",
        "reason": "not_configured",
        "positions": [],
        "current_unrealized_pnl": None,
        "stressed_unrealized_pnl": None,
        "pnl_change": None,
        "book_mutated": False,
        "live_execution": False,
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
            positions = conn.execute(
                "SELECT symbol, quantity, average_cost FROM paper_exec_positions ORDER BY symbol"
            ).fetchall()
            marks = {
                row["symbol"]: row
                for row in conn.execute("SELECT symbol, price, observed_at FROM paper_exec_marks")
            }
        finally:
            conn.close()
    held = [row for row in positions if Decimal(row["quantity"]) != 0]
    _scope(parsed, {row["symbol"] for row in held})
    rows = []
    current = Fraction(0)
    stressed = Fraction(0)
    for row in held:
        symbol = row["symbol"]
        mark = marks.get(symbol)
        shock = parsed["*"] if "*" in parsed else parsed[symbol]
        if mark is None or not ledger._fresh(account, mark["observed_at"], as_of):
            report.update(state="incomplete", reason="mark_required", positions=[])
            return report
        qty = Fraction(Decimal(row["quantity"]))
        average = Fraction(Decimal(row["average_cost"]))
        price = Fraction(Decimal(mark["price"]))
        shocked = ledger._finite_decimal(price * (1 + Fraction(shock)))
        if shocked <= 0:
            raise ValueError("invalid_stress_shock")
        now_pnl = qty * (price - average)
        next_pnl = qty * (Fraction(shocked) - average)
        current += now_pnl
        stressed += next_pnl
        rows.append({
            "symbol": symbol,
            "quantity": row["quantity"],
            "mark": mark["price"],
            "shock": ledger._canonical(shock),
            "shocked_mark": ledger._canonical(shocked),
            "current_unrealized_pnl": ledger._canonical(ledger._finite_decimal(now_pnl)),
            "stressed_unrealized_pnl": ledger._canonical(ledger._finite_decimal(next_pnl)),
        })
    report.update({
        "state": "ready",
        "reason": None,
        "positions": rows,
        "current_unrealized_pnl": ledger._canonical(ledger._finite_decimal(current)),
        "stressed_unrealized_pnl": ledger._canonical(ledger._finite_decimal(stressed)),
        "pnl_change": ledger._canonical(ledger._finite_decimal(stressed - current)),
    })
    return report


def _shocks(shocks: object) -> dict[str, Decimal]:
    if not isinstance(shocks, dict) or not shocks:
        raise ValueError("invalid_stress_shocks")
    parsed = {}
    for symbol, value in shocks.items():
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("invalid_stress_shocks")
        if isinstance(value, bool) or not isinstance(value, str):
            raise ValueError("invalid_stress_shock")
        try:
            number = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("invalid_stress_shock") from exc
        if not number.is_finite() or number <= -1 or number > 5:
            raise ValueError("invalid_stress_shock")
        parsed[symbol.strip().upper() if symbol != "*" else "*"] = number
    if "*" in parsed and len(parsed) != 1:
        raise ValueError("mixed_stress_scope")
    return parsed


def _scope(shocks: dict[str, Decimal], held: set[str]) -> None:
    if "*" in shocks:
        return
    unknown = set(shocks) - held
    if unknown:
        raise ValueError("shock_for_unheld_symbol")
    if held - set(shocks):
        raise ValueError("shock_missing")
