"""Canonical commitments for explicitly enabled Alpaca PAPER submissions.

The execution ledger owns these reservations; control-plane documents do not
approve or release exposure. Broker cumulative snapshots supply neither actual
execution IDs nor fees, so this module imports no fills, cash or positions.
Until accounting is implemented, even terminal fills retain their commitments.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from .control_plane.database import canonical, digest, identifier

EVENT = "broker_reservation_changed"
_FIELDS = frozenset({"client_order_id", "binding", "order", "response"})


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_exec_broker_reservations (
        client_order_id TEXT PRIMARY KEY, body TEXT NOT NULL)""")


def _order(order: object, client_id: str) -> dict:
    from .control_plane.broker import _saved_broker_order

    # canonical() bounds depth, size and JSON types before either persistence
    # or the order fingerprint is trusted.
    detached = json.loads(canonical(order))
    return _saved_broker_order(
        {"order": detached, "fingerprint": digest(detached)}, client_id,
    )


def broker_binding(control_db: str, order: dict) -> str:
    if not isinstance(control_db, str) or not control_db:
        raise ValueError("broker_control_database_required")
    if not isinstance(order, dict):
        raise ValueError("broker_order_contract_required")  # noqa: TRY004 - stable broker refusal API
    client_id = identifier(order.get("client_order_id"))
    saved = _order(order, client_id)
    return digest({"broker": "alpaca-paper", "control_db": control_db, "order": saved})


def _record(value: object, client_id: str | None = None) -> dict:
    from .control_plane.broker import validate_broker_snapshot

    try:
        value = json.loads(canonical(value))
        if not isinstance(value, dict) or set(value) != _FIELDS:
            raise ValueError()
        key = identifier(value["client_order_id"])
        if client_id is not None and key != client_id:
            raise ValueError()
        if not isinstance(value["binding"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", value["binding"],
        ):
            raise ValueError()
        value["order"] = _order(value["order"], key)
        if value["response"] is not None:
            validate_broker_snapshot(value["order"], value["response"])
        return value
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ValueError("broker_reservation_contract_required") from None


def read(conn: sqlite3.Connection, client_order_id: str) -> dict | None:
    key = identifier(client_order_id)
    row = conn.execute(
        "SELECT body FROM paper_exec_broker_reservations WHERE client_order_id=?", (key,),
    ).fetchone()
    if row is None:
        return None
    try:
        return _record(json.loads(row[0]), key)
    except (ValueError, TypeError, RecursionError):
        raise ValueError("broker_reservation_contract_required") from None


def rows(conn: sqlite3.Connection) -> list[dict]:
    result = []
    for row in conn.execute(
        "SELECT client_order_id,body FROM paper_exec_broker_reservations ORDER BY client_order_id"
    ):
        try:
            result.append(_record(json.loads(row[1]), row[0]))
        except (ValueError, TypeError, RecursionError):
            raise ValueError("broker_reservation_contract_required") from None
    return result


def _bound(previous: dict, binding: str, order: dict) -> None:
    if previous["binding"] != binding or previous["order"] != order:
        raise ValueError("broker_reservation_conflict")


def _store(conn: sqlite3.Connection, record: dict, *, initial: bool) -> dict:
    from . import paper_execution as ledger

    body = canonical(record).decode()
    if initial:
        conn.execute(
            "INSERT INTO paper_exec_broker_reservations VALUES (?,?)",
            (record["client_order_id"], body),
        )
    else:
        conn.execute(
            "UPDATE paper_exec_broker_reservations SET body=? WHERE client_order_id=?",
            (body, record["client_order_id"]),
        )
    ledger._append_audit(conn, EVENT, record["client_order_id"], {"reservation": record})
    return record


def reserve(conn: sqlite3.Connection, client_order_id: str, binding: str, order: dict) -> dict:
    """Persist a commitment in the caller's canonical risk transaction."""
    record = _record({
        "client_order_id": client_order_id, "binding": binding,
        "order": order, "response": None,
    })
    previous = read(conn, record["client_order_id"])
    if previous is not None:
        _bound(previous, binding, record["order"])
        return previous
    if conn.execute(
        "SELECT 1 FROM paper_exec_orders WHERE client_order_id=?", (record["client_order_id"],),
    ).fetchone() is not None:
        raise ValueError("broker_reservation_conflict")
    return _store(conn, record, initial=True)


def update(
    conn: sqlite3.Connection, client_order_id: str, binding: str, order: dict, response: dict,
) -> dict:
    """Apply monotonic verified progress, retaining filled commitments."""
    from .control_plane.broker import validate_broker_snapshot

    record = _record({
        "client_order_id": client_order_id, "binding": binding,
        "order": order, "response": response,
    })
    previous = read(conn, record["client_order_id"])
    if previous is None:
        raise ValueError("broker_reservation_required")
    _bound(previous, binding, record["order"])
    validate_broker_snapshot(record["order"], record["response"], previous=previous["response"])
    if previous == record:
        return previous
    return _store(conn, record, initial=False)


def projection_breaks(conn: sqlite3.Connection) -> list[str]:
    """Compare mutable reservation rows with their complete journal history."""
    from .control_plane.broker import validate_broker_snapshot

    try:
        expected = {}
        for row in conn.execute(
            "SELECT client_order_id,payload FROM paper_exec_audit WHERE event=? ORDER BY seq", (EVENT,),
        ):
            payload = json.loads(row[1])
            if not isinstance(payload, dict) or set(payload) != {"reservation"}:
                raise ValueError()
            record = _record(payload["reservation"], identifier(row[0]))
            key = record["client_order_id"]
            previous = expected.get(key)
            if previous is None:
                if record["response"] is not None:
                    raise ValueError()
            else:
                _bound(previous, record["binding"], record["order"])
                if record["response"] is None:
                    raise ValueError()
                validate_broker_snapshot(record["order"], record["response"], previous=previous["response"])
            expected[key] = record
        actual = {row["client_order_id"]: row for row in rows(conn)}
        if actual != expected:
            raise ValueError()
    except (ValueError, TypeError, KeyError, RecursionError, ArithmeticError):
        return ["broker_reservation_projection"]
    return []


def _quantity(record: dict) -> Decimal:
    from .control_plane.broker import TERMINAL_BROKER_STATES, money

    response = record["response"]
    if response is not None and response["status"] in TERMINAL_BROKER_STATES:
        return money(response["filled_qty"], positive=False)
    return money(record["order"]["quantity"])


def reserved(conn: sqlite3.Connection) -> tuple[Decimal, dict[str, Decimal]]:
    from . import paper_execution as ledger

    cash = Fraction(0)
    sells: dict[str, Fraction] = {}
    for record in rows(conn):
        order, qty = record["order"], Fraction(_quantity(record))
        if not qty:
            continue
        if order["side"].upper() == "BUY":
            cash += qty * Fraction(order["limit_price"])
        else:
            symbol = order["symbol"].upper()
            sells[symbol] = sells.get(symbol, Fraction(0)) + qty
    return ledger._finite_decimal(cash), {
        symbol: ledger._finite_decimal(qty) for symbol, qty in sells.items()
    }


def exposure_orders(conn: sqlite3.Connection) -> list[dict]:
    from . import paper_execution as ledger

    result = []
    for record in rows(conn):
        qty = _quantity(record)
        if qty:
            order = record["order"]
            result.append({
                "symbol": order["symbol"].upper(), "side": order["side"].upper(),
                "leaves_quantity": ledger._canonical(qty), "price": order["limit_price"],
            })
    return result


def open_buy_exposure(conn: sqlite3.Connection, symbol: str) -> tuple[Decimal, Decimal]:
    from . import paper_execution as ledger

    position, gross = Fraction(0), Fraction(0)
    for order in exposure_orders(conn):
        if order["side"] == "BUY":
            notional = Fraction(order["leaves_quantity"]) * Fraction(order["price"])
            gross += notional
            if order["symbol"] == symbol.upper():
                position += notional
    return ledger._finite_decimal(position), ledger._finite_decimal(gross)


def accounting_pending(conn: sqlite3.Connection) -> bool:
    return any(
        record["response"] is not None and Decimal(record["response"]["filled_qty"]) > 0
        for record in rows(conn)
    )


def configured_inventory_reason(conn: sqlite3.Connection) -> str | None:
    """Fail closed when configured transport evidence lacks matching reserves.

    Read the configured control store without acquiring a second write lock.
    The execution and control tables may share the caller's SQLite database.
    Every retained intent must agree with a canonical reservation and its
    progress. Mutable retained JSON alone cannot establish terminality or
    absence of risk, including a legacy record claiming zero terminal fills.
    """
    from .control_plane.broker import (
        _saved_broker_order,
        validate_broker_snapshot,
    )

    configured = os.getenv("OSIRIS_CONTROL_DB", "")
    if not configured:
        return None
    reason = "broker_reservation_required_reconcile_first"
    control = None
    try:
        path = Path(configured).resolve()
        if not path.is_file():
            return reason
        main_path = next(
            (row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"),
            "",
        )
        if main_path and Path(main_path).resolve() == path:
            source = conn
        else:
            control = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
            source = control
        # Oversized persisted bodies cannot be admitted through Database.put.
        # Do not materialize them merely to reject a corrupt transport record.
        inventory = source.execute(
            """SELECT id,
                      CASE WHEN length(CAST(body AS BLOB)) <= 1048576 THEN body END
               FROM cp_documents WHERE collection='broker-orders' ORDER BY id"""
        )
        for index, row in enumerate(inventory):
            if index >= 100_000 or not isinstance(row[1], str):
                return reason
            client_id = identifier(row[0])
            state = json.loads(row[1])
            canonical(state)
            order = _saved_broker_order(state, client_id)
            if state.get("status") not in {"submitting", "acknowledged"}:
                return reason
            response = state.get("response")
            if state["status"] == "acknowledged" or "response" in state:
                validate_broker_snapshot(order, response)
            previous = read(conn, client_id)
            if previous is None:
                return reason
            if (
                previous["binding"] != broker_binding(str(path), order)
                or previous["order"] != order
                or previous["response"] != response
            ):
                return reason
        return None
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, RecursionError):
        return reason
    finally:
        if control is not None:
            control.close()


def public(conn: sqlite3.Connection) -> list[dict]:
    from . import paper_execution as ledger

    result = []
    for record in rows(conn):
        order, response = record["order"], record["response"]
        filled = Decimal(0) if response is None else Decimal(response["filled_qty"])
        result.append({
            "client_order_id": record["client_order_id"], "binding": record["binding"],
            "symbol": order["symbol"].upper(), "side": order["side"].upper(),
            "quantity": ledger._canonical(Decimal(order["quantity"])),
            "reserved_quantity": ledger._canonical(_quantity(record)),
            "filled_quantity": ledger._canonical(filled),
            "status": "submitting" if response is None else response["status"],
            "accounting_pending": filled > 0,
        })
    return result


def synchronize_broker_reservation(binding: str, order: dict, response: dict) -> dict:
    """Retain known external commitments, including legacy orders, after GET.

    Reconciliation records possibly sent risk even if current limits would
    reject a fresh order. No network request or simulated fill occurs here.
    """
    from . import paper_execution as ledger

    client_id = identifier(order.get("client_order_id"))
    with ledger._lock:
        conn = ledger._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            ledger._assert_ready(conn, allow_broker_break=True)
            if read(conn, client_id) is None:
                reserve(conn, client_id, binding, order)
            record = update(conn, client_id, binding, order, response)
            ledger._assert_ready(conn, allow_broker_break=True)
            ledger._commit(conn)
            return record
        except Exception:
            ledger._rollback(conn)
            raise
        finally:
            conn.close()
