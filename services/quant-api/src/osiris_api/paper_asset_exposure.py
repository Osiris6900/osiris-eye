"""Cross-asset classification of the canonical paper book.

Each holding is joined to the instrument-master asset class. Edges mean that
classification, not a correlation, beta, or hedge. Caller portfolios are ignored.
"""
from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from typing import Any

from .instrument_master import InstrumentMasterStore, assess_order, instrument_db_path
from . import paper_execution as ledger


def paper_asset_exposure(as_of: object) -> dict[str, Any]:
    when = ledger._parse_time(as_of)
    if when is None or not isinstance(as_of, str):
        raise ValueError("invalid_asset_exposure_time")
    report = {
        "state": "unavailable",
        "reason": "not_configured",
        "asset_classes": [],
        "nodes": [],
        "edges": [],
        "gross_exposure": None,
        "correlation_estimated": False,
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
    classes: dict[str, Fraction] = {}
    nodes = []
    edges = []
    for row in positions:
        symbol = row["symbol"]
        mark = marks.get(symbol)
        if mark is None or not ledger._fresh(account, mark["observed_at"], as_of):
            report.update(state="incomplete", reason="mark_required")
            return report
        asset_class = _asset_class(symbol)
        if asset_class is None:
            report.update(state="incomplete", reason="unresolved_asset_class")
            return report
        exposure = Fraction(abs(Decimal(row["quantity"]))) * Fraction(Decimal(mark["price"]))
        classes[asset_class] = classes.get(asset_class, Fraction(0)) + exposure
        nodes.append({
            "id": symbol, "kind": "position", "asset_class": asset_class,
            "exposure": ledger._canonical(ledger._finite_decimal(exposure)),
        })
        edges.append({"from": symbol, "to": asset_class, "relation": "classified_as"})
    class_rows = []
    for name, amount in sorted(classes.items()):
        text = ledger._canonical(ledger._finite_decimal(amount))
        class_rows.append({"asset_class": name, "exposure": text})
        nodes.append({"id": name, "kind": "asset_class", "exposure": text})
    gross = sum(classes.values(), Fraction(0))
    report.update({
        "state": "ready",
        "reason": None,
        "asset_classes": class_rows,
        "nodes": nodes,
        "edges": edges,
        "gross_exposure": ledger._canonical(ledger._finite_decimal(gross)),
    })
    return report


def _asset_class(symbol: str) -> str | None:
    decision = assess_order(symbol)
    if decision.get("reason") == "ambiguous_instrument" or not decision.get("instrument_id"):
        return None
    store = InstrumentMasterStore(instrument_db_path())
    store.ensure_seeded()
    got = store.get(decision["instrument_id"])
    if got is None:
        return None
    value = got["instrument"].get("asset_class")
    return value if isinstance(value, str) and value else None
