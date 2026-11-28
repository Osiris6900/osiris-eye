"""Execution records for the paper book.

Each accepted order is one paper route. The limit is the decision price and
the fills are the execution. Shortfall is exact versus that limit. This does
not choose a broker, a venue route, or a live order.
"""
from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from typing import Any

from .instrument_master import InstrumentMasterStore, assess_order, instrument_db_path
from . import paper_execution as ledger

_ROUTED_STATES = frozenset({"accepted", "partially_filled", "filled", "cancelled"})


def paper_route_records() -> dict[str, Any]:
    report = {
        "state": "unavailable",
        "reason": "not_configured",
        "routes": [],
        "route": "paper_book",
        "live_routing_enabled": False,
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
            if conn.execute("SELECT 1 FROM paper_exec_meta WHERE id=1").fetchone() is None:
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
                """SELECT client_order_id, symbol, side, price, state, filled_quantity
                   FROM paper_exec_orders ORDER BY client_order_id"""
            ).fetchall()
            fills = conn.execute(
                """SELECT client_order_id, quantity, price FROM paper_exec_fills ORDER BY seq"""
            ).fetchall()
        finally:
            conn.close()
    by_order: dict[str, list] = {}
    for row in fills:
        by_order.setdefault(row["client_order_id"], []).append(row)
    routes = []
    for order in orders:
        if order["state"] not in _ROUTED_STATES:
            continue
        routes.append(_record(order, by_order.get(order["client_order_id"], [])))
    report.update(state="ready", reason=None, routes=routes)
    return report


def _record(order, fills) -> dict[str, Any]:
    limit = Decimal(order["price"])
    quantity = Fraction(0)
    notional = Fraction(0)
    for fill in fills:
        qty = Fraction(Decimal(fill["quantity"]))
        quantity += qty
        notional += qty * Fraction(Decimal(fill["price"]))
    average = None if quantity == 0 else notional / quantity
    shortfall = None
    if average is not None and limit > 0:
        signed = (average - Fraction(limit)) if order["side"] == "BUY" else (Fraction(limit) - average)
        shortfall = _ratio(signed / Fraction(limit) * 10000)
    venue, venue_status = _venue(order["symbol"])
    return {
        "client_order_id": order["client_order_id"],
        "symbol": order["symbol"],
        "side": order["side"],
        "state": order["state"],
        "limit_price": order["price"],
        "filled_quantity": order["filled_quantity"],
        "average_fill_price": None if average is None else _ratio(average),
        "shortfall_bps": shortfall,
        "venue": venue,
        "venue_status": venue_status,
        "route": "paper_book",
        "live_routing_enabled": False,
    }


def _venue(symbol: str) -> tuple[str | None, str]:
    decision = assess_order(symbol)
    if decision.get("reason") == "ambiguous_instrument" or not decision.get("instrument_id"):
        return None, "unresolved"
    store = InstrumentMasterStore(instrument_db_path())
    store.ensure_seeded()
    got = store.get(decision["instrument_id"])
    if got is None:
        return None, "unresolved"
    return got["instrument"]["venue"], "master"


def _ratio(value: Fraction) -> str:
    try:
        return ledger._canonical(ledger._finite_decimal(value))
    except (ValueError, ArithmeticError):
        return f"{value.numerator}/{value.denominator}"
