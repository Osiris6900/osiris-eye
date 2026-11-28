"""Counterparty exposure of the canonical paper book.

The only counterparty identity the book has is the instrument-master venue.
Caller exposures are not accepted. Creditworthiness is not assessed.
"""
from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from typing import Any

from .instrument_master import InstrumentMasterStore, assess_order, instrument_db_path
from . import paper_execution as ledger


def paper_counterparty_exposure(as_of: object) -> dict[str, Any]:
    when = ledger._parse_time(as_of)
    if when is None or not isinstance(as_of, str):
        raise ValueError("invalid_counterparty_time")
    report = {
        "state": "unavailable",
        "reason": "not_configured",
        "counterparties": [],
        "gross_exposure": None,
        "creditworthiness_assessed": False,
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
            positions = [
                row for row in conn.execute(
                    "SELECT symbol, quantity FROM paper_exec_positions ORDER BY symbol"
                )
                if Decimal(row["quantity"]) != 0
            ]
            marks = {
                row["symbol"]: row
                for row in conn.execute("SELECT symbol, price, observed_at FROM paper_exec_marks")
            }
        finally:
            conn.close()
    buckets: dict[str, Fraction] = {}
    lines = []
    for row in positions:
        symbol = row["symbol"]
        mark = marks.get(symbol)
        if mark is None or not ledger._fresh(account, mark["observed_at"], as_of):
            report.update(state="incomplete", reason="mark_required", counterparties=[], gross_exposure=None)
            return report
        venue = _venue(symbol)
        if venue is None:
            report.update(state="incomplete", reason="unresolved_counterparty", counterparties=[], gross_exposure=None)
            return report
        exposure = Fraction(abs(Decimal(row["quantity"]))) * Fraction(Decimal(mark["price"]))
        buckets[venue] = buckets.get(venue, Fraction(0)) + exposure
        lines.append({
            "symbol": symbol,
            "counterparty": venue,
            "quantity": row["quantity"],
            "mark": mark["price"],
            "exposure": ledger._canonical(ledger._finite_decimal(exposure)),
        })
    gross = sum(buckets.values(), Fraction(0))
    report.update({
        "state": "ready",
        "reason": None,
        "positions": lines,
        "counterparties": [
            {"counterparty": name, "exposure": ledger._canonical(ledger._finite_decimal(amount))}
            for name, amount in sorted(buckets.items())
        ],
        "gross_exposure": ledger._canonical(ledger._finite_decimal(gross)),
    })
    return report


def _venue(symbol: str) -> str | None:
    decision = assess_order(symbol)
    if decision.get("reason") == "ambiguous_instrument" or not decision.get("instrument_id"):
        return None
    store = InstrumentMasterStore(instrument_db_path())
    store.ensure_seeded()
    got = store.get(decision["instrument_id"])
    if got is None:
        return None
    venue = got["instrument"].get("venue")
    return venue if isinstance(venue, str) and venue else None
