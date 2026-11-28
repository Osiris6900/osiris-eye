"""Cash collateral for the canonical paper book.

The book does not borrow. Free cash is cash minus open buy reservations.
A caller cannot substitute a balance. This is not a broker margin schedule.
"""
from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from typing import Any

from . import paper_execution as ledger


def paper_collateral(as_of: object) -> dict[str, Any]:
    when = ledger._parse_time(as_of)
    if when is None or not isinstance(as_of, str):
        raise ValueError("invalid_collateral_time")
    report = {
        "state": "unavailable",
        "reason": "not_configured",
        "cash": None,
        "reserved_cash": None,
        "free_cash": None,
        "margin_loan": "0",
        "margin_model": "cash_fully_funded",
        "long_market_value": None,
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
            reserved, _sells = ledger._reserved(conn)
            positions = conn.execute(
                "SELECT symbol, quantity FROM paper_exec_positions ORDER BY symbol"
            ).fetchall()
            marks = {
                row["symbol"]: row
                for row in conn.execute("SELECT symbol, price, observed_at FROM paper_exec_marks")
            }
        finally:
            conn.close()
    cash = Decimal(account["cash"])
    free = ledger._finite_decimal(Fraction(cash) - Fraction(reserved))
    if cash < 0 or free < 0 or reserved < 0:
        report.update(
            state="breach",
            reason="cash_collateral_breach",
            cash=ledger._canonical(cash),
            reserved_cash=ledger._canonical(reserved),
            free_cash=ledger._canonical(free),
        )
        return report
    market, complete = Fraction(0), True
    for row in positions:
        qty = Decimal(row["quantity"])
        if qty == 0:
            continue
        mark = marks.get(row["symbol"])
        if mark is None or not ledger._fresh(account, mark["observed_at"], as_of):
            complete = False
            break
        market += Fraction(qty) * Fraction(Decimal(mark["price"]))
    report.update({
        "state": "ready" if complete else "incomplete",
        "reason": None if complete else "mark_required",
        "cash": ledger._canonical(cash),
        "reserved_cash": ledger._canonical(reserved),
        "free_cash": ledger._canonical(free),
        "long_market_value": ledger._canonical(ledger._finite_decimal(market)) if complete else None,
    })
    return report
