"""Durable paper execution. Live broker routing is disabled and not configurable.

Orders are risk-gated, idempotent, and stored in SQLite. A working order can
fill in parts. Each move follows the paper state machine. A crash before commit
leaves the previous state in place. Restart reads the same cash, positions,
P&L, and audit chain. This is a simulation ledger, not a production trading system.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from .risk import RiskLimits, check_execution

# There is no branch that submits a broker order. A true value still refuses.
LIVE_EXECUTION_ENABLED = False

# Paper-only lifecycle. "new" is transient and is never stored.
ORDER_STATES = (
    "new", "accepted", "partially_filled", "filled", "cancelled", "rejected",
)
OPEN_ORDER_STATES = frozenset({"accepted", "partially_filled"})
_ORDER_TRANSITIONS = {
    "new": frozenset({"accepted", "rejected"}),
    "accepted": frozenset({"partially_filled", "filled", "cancelled"}),
    "partially_filled": frozenset({"partially_filled", "filled", "cancelled"}),
    "filled": frozenset(),
    "cancelled": frozenset(),
    "rejected": frozenset(),
}

router = APIRouter()
_lock = Lock()


def order_transition(current: str, target: str) -> str:
    """Return ``target`` when the paper-order move is legal.

    This does not submit a broker order. Illegal moves raise
    ``invalid_order_transition``.
    """
    if target not in _ORDER_TRANSITIONS.get(current, frozenset()):
        raise ValueError("invalid_order_transition")
    return target


def _audit_live_refusal() -> None:
    """Record a refusal when an account exists. Never enables a broker path."""
    path = os.getenv("OSIRIS_PAPER_EXECUTION_DB")
    if not path or path == ":memory:":
        return
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM paper_exec_meta WHERE id=1").fetchone() is None:
                conn.rollback()
                return
            _verify_audit(conn)
            if _reconcile_breaks(conn):
                conn.rollback()
                return
            _append_audit(conn, "live_execution_refused", None, {
                "live_execution": False, "broker_order_submitted": False,
            })
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            conn.close()


def _refuse_live(live_execution: object) -> None:
    if (
        LIVE_EXECUTION_ENABLED is not False
        or live_execution is True
        or os.getenv("OSIRIS_ENABLE_LIVE_EXECUTION")
    ):
        _audit_live_refusal()
        raise ValueError("live_execution_disabled")


def _database() -> str:
    path = os.getenv("OSIRIS_PAPER_EXECUTION_DB")
    if not path or path == ":memory:":
        raise ValueError("paper_execution_db_not_configured")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return path


def _number(value: Any, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(
        value, (str, int, Decimal)
    ):
        raise ValueError("invalid_number")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid_number") from exc
    if not number.is_finite() or (positive and number <= 0) or number < 0:
        raise ValueError("invalid_number")
    return number


def _canonical(number: Decimal) -> str:
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _as_float(number: Decimal) -> float:
    value = float(number)
    if not math.isfinite(value):
        raise ValueError("invalid_numeric_input")
    return value


def _canon_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _row_hash(prev: str, seq: int, event: str, client_order_id: str | None, payload: str) -> str:
    material = _canon_json({
        "prev": prev, "seq": seq, "event": event,
        "client_order_id": client_order_id, "payload": payload,
    })
    return hashlib.sha256(material.encode()).hexdigest()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_database(), timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_exec_meta (
        id INTEGER PRIMARY KEY CHECK(id=1),
        initial_cash TEXT NOT NULL, cash TEXT NOT NULL, realized_pnl TEXT NOT NULL,
        kill_switch INTEGER NOT NULL,
        max_order_notional TEXT NOT NULL, max_position_notional TEXT NOT NULL,
        max_daily_loss TEXT NOT NULL, max_gross_exposure TEXT NOT NULL,
        max_market_age_seconds TEXT NOT NULL, max_future_skew_seconds TEXT NOT NULL,
        unrealized_pnl TEXT NOT NULL DEFAULT '0')""")
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_exec_positions (
        symbol TEXT PRIMARY KEY, quantity TEXT NOT NULL, average_cost TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_exec_orders (
        client_order_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, side TEXT NOT NULL,
        quantity TEXT NOT NULL, price TEXT NOT NULL, fee TEXT NOT NULL,
        status TEXT NOT NULL, reason TEXT NOT NULL, limits_evaluated INTEGER NOT NULL,
        risk_decision_id TEXT NOT NULL, created_at TEXT NOT NULL,
        state TEXT NOT NULL, filled_quantity TEXT NOT NULL, leaves_quantity TEXT NOT NULL,
        execution_mode TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_exec_fills (
        seq INTEGER PRIMARY KEY, client_order_id TEXT NOT NULL,
        client_fill_id TEXT NOT NULL UNIQUE,
        symbol TEXT NOT NULL, side TEXT NOT NULL, quantity TEXT NOT NULL,
        price TEXT NOT NULL, fee TEXT NOT NULL, realized_pnl TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_exec_audit (
        seq INTEGER PRIMARY KEY, client_order_id TEXT, event TEXT NOT NULL,
        payload TEXT NOT NULL, prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_exec_marks (
        symbol TEXT PRIMARY KEY, price TEXT NOT NULL, observed_at TEXT NOT NULL)""")
    _migrate(conn)
    return conn


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn: sqlite3.Connection) -> None:
    """Open ledgers written before the order-lifecycle columns."""
    meta = _column_names(conn, "paper_exec_meta")
    if "unrealized_pnl" not in meta:
        conn.execute(
            "ALTER TABLE paper_exec_meta ADD COLUMN unrealized_pnl TEXT NOT NULL DEFAULT '0'"
        )
    orders = _column_names(conn, "paper_exec_orders")
    added_order_columns = False
    for name, ddl in (
        ("state", "state TEXT NOT NULL DEFAULT ''"),
        ("filled_quantity", "filled_quantity TEXT NOT NULL DEFAULT '0'"),
        ("leaves_quantity", "leaves_quantity TEXT NOT NULL DEFAULT '0'"),
        ("execution_mode", "execution_mode TEXT NOT NULL DEFAULT 'immediate'"),
    ):
        if name not in orders:
            conn.execute(f"ALTER TABLE paper_exec_orders ADD COLUMN {ddl}")
            added_order_columns = True
    if added_order_columns:
        conn.execute(
            """UPDATE paper_exec_orders SET
               state = CASE status WHEN 'filled' THEN 'filled' ELSE 'rejected' END,
               filled_quantity = CASE status WHEN 'filled' THEN quantity ELSE '0' END,
               leaves_quantity = '0',
               execution_mode = 'immediate'"""
        )
    fills = _column_names(conn, "paper_exec_fills")
    if "client_fill_id" not in fills:
        conn.execute("""CREATE TABLE paper_exec_fills_v2 (
            seq INTEGER PRIMARY KEY, client_order_id TEXT NOT NULL,
            client_fill_id TEXT NOT NULL UNIQUE,
            symbol TEXT NOT NULL, side TEXT NOT NULL, quantity TEXT NOT NULL,
            price TEXT NOT NULL, fee TEXT NOT NULL, realized_pnl TEXT NOT NULL)""")
        for row in conn.execute("SELECT * FROM paper_exec_fills"):
            conn.execute(
                """INSERT INTO paper_exec_fills_v2(
                    seq, client_order_id, client_fill_id, symbol, side, quantity, price, fee, realized_pnl)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (row["seq"], row["client_order_id"], row["client_order_id"], row["symbol"],
                 row["side"], row["quantity"], row["price"], row["fee"], row["realized_pnl"]),
            )
        conn.execute("DROP TABLE paper_exec_fills")
        conn.execute("ALTER TABLE paper_exec_fills_v2 RENAME TO paper_exec_fills")


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _limits_from_row(row: sqlite3.Row) -> RiskLimits:
    return RiskLimits(
        max_order_notional=_as_float(Decimal(row["max_order_notional"])),
        max_position_notional=_as_float(Decimal(row["max_position_notional"])),
        max_daily_loss=_as_float(Decimal(row["max_daily_loss"])),
        max_gross_exposure=_as_float(Decimal(row["max_gross_exposure"])),
    )


def _validate_limits(limits: RiskLimits) -> None:
    values = (
        limits.max_order_notional, limits.max_position_notional,
        limits.max_daily_loss, limits.max_gross_exposure,
    )
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        raise ValueError("invalid_risk_limits")
    try:
        finite = all(Decimal(str(value)).is_finite() for value in values)
    except InvalidOperation as exc:
        raise ValueError("invalid_risk_limits") from exc
    if (
        not finite
        or limits.max_order_notional <= 0
        or limits.max_position_notional <= 0
        or limits.max_daily_loss < 0
        or limits.max_gross_exposure <= 0
    ):
        raise ValueError("invalid_risk_limits")


def _append_audit(
    conn: sqlite3.Connection, event: str, client_order_id: str | None, payload: dict[str, Any],
) -> tuple[int, str]:
    encoded = _canon_json(payload)
    previous = conn.execute(
        "SELECT seq, row_hash FROM paper_exec_audit ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    seq = 1 if previous is None else int(previous["seq"]) + 1
    prev = "GENESIS" if previous is None else previous["row_hash"]
    digest = _row_hash(prev, seq, event, client_order_id, encoded)
    conn.execute(
        """INSERT INTO paper_exec_audit(seq, client_order_id, event, payload, prev_hash, row_hash)
           VALUES (?,?,?,?,?,?)""",
        (seq, client_order_id, event, encoded, prev, digest),
    )
    return seq, digest


def _verify_audit(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT seq, client_order_id, event, payload, prev_hash, row_hash "
        "FROM paper_exec_audit ORDER BY seq"
    ).fetchall()
    prev = "GENESIS"
    for expected, row in enumerate(rows, start=1):
        if int(row["seq"]) != expected:
            raise ValueError("audit_chain_corrupt")
        digest = _row_hash(prev, expected, row["event"], row["client_order_id"], row["payload"])
        if row["prev_hash"] != prev or row["row_hash"] != digest:
            raise ValueError("audit_chain_corrupt")
        prev = row["row_hash"]


def _apply(
    cash: Decimal, held: Decimal, avg: Decimal, side: str, qty: Decimal, px: Decimal, fee: Decimal,
) -> tuple[Decimal, Decimal, Decimal, Decimal] | None:
    if side == "BUY":
        total = qty * px + fee
        if total > cash:
            return None
        new_qty = held + qty
        new_avg = (held * avg + qty * px + fee) / new_qty
        return cash - total, new_qty, new_avg, Decimal("0")
    if qty > held:
        return None
    proceeds = qty * px - fee
    if proceeds < 0:
        return None
    delta = qty * (px - avg) - fee
    return cash + proceeds, held - qty, avg, delta


def _recompute(conn: sqlite3.Connection) -> tuple[Decimal, Decimal, dict[str, tuple[Decimal, Decimal]]]:
    account = conn.execute("SELECT * FROM paper_exec_meta WHERE id=1").fetchone()
    if account is None:
        raise ValueError("account_not_initialized")
    cash = Decimal(account["initial_cash"])
    realized = Decimal("0")
    positions: dict[str, tuple[Decimal, Decimal]] = {}
    fills = conn.execute("SELECT * FROM paper_exec_fills ORDER BY seq").fetchall()
    for fill in fills:
        held, avg = positions.get(fill["symbol"], (Decimal("0"), Decimal("0")))
        applied = _apply(
            cash, held, avg, fill["side"], Decimal(fill["quantity"]),
            Decimal(fill["price"]), Decimal(fill["fee"]),
        )
        if applied is None:
            raise ValueError("reconciliation_break")
        cash, new_qty, new_avg, delta = applied
        realized += delta
        if new_qty == 0:
            positions.pop(fill["symbol"], None)
        else:
            positions[fill["symbol"]] = (new_qty, new_avg)
        if Decimal(fill["realized_pnl"]) != delta:
            raise ValueError("reconciliation_break")
    return cash, realized, positions


def _marks(conn: sqlite3.Connection) -> dict[str, Decimal]:
    return {
        row["symbol"]: Decimal(row["price"])
        for row in conn.execute("SELECT symbol, price FROM paper_exec_marks")
    }


def _expected_unrealized(
    positions: dict[str, tuple[Decimal, Decimal]], marks: dict[str, Decimal],
) -> tuple[Decimal, list[str]]:
    """Mark long paper inventory. An unmarked name contributes zero and is listed."""
    total = Decimal("0")
    unmarked: list[str] = []
    for symbol, (qty, avg) in sorted(positions.items()):
        if symbol not in marks:
            unmarked.append(symbol)
            continue
        total += qty * (marks[symbol] - avg)
    return total, unmarked


def _order_breaks(conn: sqlite3.Connection) -> list[str]:
    breaks: list[str] = []
    filled_by_order: dict[str, Decimal] = {}
    fee_by_order: dict[str, Decimal] = {}
    seen_orders: set[str] = set()
    for fill in conn.execute("SELECT * FROM paper_exec_fills ORDER BY seq"):
        qty = Decimal(fill["quantity"])
        price = Decimal(fill["price"])
        fee = Decimal(fill["fee"])
        if qty <= 0 or price <= 0 or fee < 0:
            breaks.append(f"fill_economics:{fill['client_fill_id']}")
        seen_orders.add(fill["client_order_id"])
        filled_by_order[fill["client_order_id"]] = (
            filled_by_order.get(fill["client_order_id"], Decimal("0")) + qty
        )
        fee_by_order[fill["client_order_id"]] = (
            fee_by_order.get(fill["client_order_id"], Decimal("0")) + fee
        )
    known: set[str] = set()
    for order in conn.execute("SELECT * FROM paper_exec_orders"):
        key = order["client_order_id"]
        known.add(key)
        state = order["state"]
        qty = Decimal(order["quantity"])
        filled = Decimal(order["filled_quantity"])
        leaves = Decimal(order["leaves_quantity"])
        actual = filled_by_order.get(key, Decimal("0"))
        if state not in ORDER_STATES or state == "new":
            breaks.append(f"order_state:{key}")
        if actual != filled:
            breaks.append(f"filled_quantity:{key}")
        if fee_by_order.get(key, Decimal("0")) > Decimal(order["fee"]):
            breaks.append(f"fill_fee:{key}")
        if state == "rejected" and (filled != 0 or leaves != 0):
            breaks.append(f"rejected_residue:{key}")
        elif state == "accepted" and (filled != 0 or leaves != qty):
            breaks.append(f"accepted_residue:{key}")
        elif state == "partially_filled" and (
            filled <= 0 or leaves <= 0 or filled + leaves != qty
        ):
            breaks.append(f"partial_residue:{key}")
        elif state == "filled" and (leaves != 0 or filled != qty):
            breaks.append(f"filled_residue:{key}")
        elif state == "cancelled" and (leaves != 0 or filled > qty):
            breaks.append(f"cancelled_residue:{key}")
        if order["execution_mode"] not in ("immediate", "working"):
            breaks.append(f"execution_mode:{key}")
        for fill in conn.execute(
            "SELECT * FROM paper_exec_fills WHERE client_order_id=?", (key,)
        ):
            if fill["symbol"] != order["symbol"] or fill["side"] != order["side"]:
                breaks.append(f"fill_order_mismatch:{fill['client_fill_id']}")
            worse = (
                order["side"] == "BUY" and Decimal(fill["price"]) > Decimal(order["price"])
            ) or (
                order["side"] == "SELL" and Decimal(fill["price"]) < Decimal(order["price"])
            )
            if worse:
                breaks.append(f"fill_price:{fill['client_fill_id']}")
    for key in seen_orders - known:
        breaks.append(f"orphan_fill:{key}")
    return breaks


def _reconcile_breaks(conn: sqlite3.Connection) -> list[str]:
    account = conn.execute("SELECT * FROM paper_exec_meta WHERE id=1").fetchone()
    if account is None:
        return []
    try:
        cash, realized, positions = _recompute(conn)
    except ValueError:
        return ["recompute_failed"]
    breaks = []
    if Decimal(account["cash"]) != cash:
        breaks.append("cash")
    if Decimal(account["realized_pnl"]) != realized:
        breaks.append("realized_pnl")
    stored = {
        row["symbol"]: (Decimal(row["quantity"]), Decimal(row["average_cost"]))
        for row in conn.execute("SELECT * FROM paper_exec_positions")
    }
    if set(stored) != set(positions):
        breaks.append("position_symbols")
    for symbol, (qty, avg) in positions.items():
        if stored.get(symbol) != (qty, avg):
            breaks.append(f"position:{symbol}")
    expected_unrealized, _unmarked = _expected_unrealized(positions, _marks(conn))
    recorded_unrealized = Decimal(account["unrealized_pnl"]) if "unrealized_pnl" in account.keys() else Decimal("0")
    if recorded_unrealized != expected_unrealized:
        breaks.append("unrealized_pnl")
    breaks.extend(_order_breaks(conn))
    return breaks


def _assert_ready(conn: sqlite3.Connection) -> None:
    account = conn.execute("SELECT 1 FROM paper_exec_meta WHERE id=1").fetchone()
    if account is None:
        raise ValueError("account_not_initialized")
    _verify_audit(conn)
    if _reconcile_breaks(conn):
        raise ValueError("reconciliation_break")


def _book(conn: sqlite3.Connection, symbol: str, price: Decimal) -> tuple[Decimal, Decimal]:
    symbol_notional = Decimal("0")
    gross = Decimal("0")
    marks = _marks(conn)
    for row in conn.execute("SELECT * FROM paper_exec_positions"):
        qty = Decimal(row["quantity"])
        mark = price if row["symbol"] == symbol else marks.get(
            row["symbol"], Decimal(row["average_cost"])
        )
        gross += abs(qty * mark)
        if row["symbol"] == symbol:
            symbol_notional = qty * price
    return symbol_notional, gross


def _public(order: sqlite3.Row, fill: sqlite3.Row | None, *, replay: bool) -> dict[str, Any]:
    body = {
        "client_order_id": order["client_order_id"],
        "status": "idempotent_replay" if replay else order["status"],
        "original_status": order["status"],
        "state": order["state"],
        "filled_quantity": order["filled_quantity"],
        "leaves_quantity": order["leaves_quantity"],
        "execution_mode": order["execution_mode"],
        "reason": order["reason"],
        "limits_evaluated": bool(order["limits_evaluated"]),
        "risk_decision_id": order["risk_decision_id"],
        "fill": None if fill is None else {
            "seq": int(fill["seq"]),
            "client_fill_id": fill["client_fill_id"],
            "quantity": fill["quantity"],
            "price": fill["price"],
            "realized_pnl": fill["realized_pnl"],
        },
        "live_execution": False,
        "broker_order_submitted": False,
        "mode": "paper_only",
    }
    return body


def _position(conn: sqlite3.Connection, symbol: str) -> tuple[Decimal, Decimal]:
    row = conn.execute(
        "SELECT quantity, average_cost FROM paper_exec_positions WHERE symbol=?", (symbol,)
    ).fetchone()
    if row is None:
        return Decimal("0"), Decimal("0")
    return Decimal(row["quantity"]), Decimal(row["average_cost"])


def _charged_fee(conn: sqlite3.Connection, client_order_id: str) -> Decimal:
    total = Decimal("0")
    for row in conn.execute(
        "SELECT fee FROM paper_exec_fills WHERE client_order_id=?", (client_order_id,)
    ):
        total += Decimal(row["fee"])
    return total


def _reserved(conn: sqlite3.Connection) -> tuple[Decimal, dict[str, Decimal]]:
    """Cash and shares already committed to open paper orders."""
    cash = Decimal("0")
    sells: dict[str, Decimal] = {}
    rows = conn.execute(
        """SELECT * FROM paper_exec_orders
           WHERE state IN ('accepted', 'partially_filled')"""
    )
    for row in rows:
        leaves = Decimal(row["leaves_quantity"])
        remaining_fee = Decimal(row["fee"]) - _charged_fee(conn, row["client_order_id"])
        if remaining_fee < 0:
            remaining_fee = Decimal("0")
        if row["side"] == "BUY":
            cash += leaves * Decimal(row["price"]) + remaining_fee
        else:
            sells[row["symbol"]] = sells.get(row["symbol"], Decimal("0")) + leaves
    return cash, sells


def _open_buy_exposure(conn: sqlite3.Connection, symbol: str) -> tuple[Decimal, Decimal]:
    symbol_notional = Decimal("0")
    gross = Decimal("0")
    rows = conn.execute(
        """SELECT symbol, leaves_quantity, price FROM paper_exec_orders
           WHERE state IN ('accepted', 'partially_filled') AND side='BUY'"""
    )
    for row in rows:
        notion = Decimal(row["leaves_quantity"]) * Decimal(row["price"])
        gross += notion
        if row["symbol"] == symbol:
            symbol_notional += notion
    return symbol_notional, gross


def execution_context(*, symbol: Any, price: Any, side: str = "BUY") -> dict[str, Any]:
    """Read authoritative paper risk inputs from one consistent ledger snapshot.

    This is proposal context, not an approval: submission and working fills
    independently recheck the persisted account inside their write transaction.
    """
    normalized = _normalize_symbol(symbol)
    px = _number(price, positive=True)
    if not isinstance(side, str) or side.upper() not in ("BUY", "SELL"):
        raise ValueError("invalid_side")
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN")
            _assert_ready(conn)
            account = conn.execute("SELECT * FROM paper_exec_meta WHERE id=1").fetchone()
            position, gross = _book(conn, normalized, px)
            if side.upper() == "BUY":
                pending_position, pending_gross = _open_buy_exposure(conn, normalized)
                position += pending_position
                gross += pending_gross
            limits = _limits_from_row(account)
            return {
                "current_position_notional": _canonical(position),
                "current_gross_exposure": _canonical(gross),
                "daily_pnl": account["realized_pnl"],
                "daily_loss_basis": "realized_pnl",
                "cash": account["cash"],
                "kill_switch_active": bool(account["kill_switch"]),
                "limits": {
                    name: getattr(limits, name) for name in (
                        "max_order_notional", "max_position_notional", "max_daily_loss",
                        "max_gross_exposure",
                    )
                },
                "max_market_age_seconds": account["max_market_age_seconds"],
                "max_future_skew_seconds": account["max_future_skew_seconds"],
                "live_execution": False,
                "broker_order_submitted": False,
                "mode": "paper_only",
            }
        finally:
            conn.rollback()
            conn.close()


def _fill_risk_reason(
    conn: sqlite3.Connection, account: sqlite3.Row, order: sqlite3.Row,
    quantity: Decimal, price: Decimal,
) -> str | None:
    """An accepted order is not a permanent exemption from current risk limits."""
    position, gross = _book(conn, order["symbol"], price)
    if order["side"] == "BUY":
        pending_position, pending_gross = _open_buy_exposure(conn, order["symbol"])
        # Replace this fill's reservation with its actual execution notional.
        released = quantity * Decimal(order["price"])
        position += pending_position - released
        gross += pending_gross - released
    try:
        approved, reason, _evaluated = check_execution(
            side=order["side"], quantity=_as_float(quantity), price=_as_float(price),
            current_position_notional=_as_float(position),
            current_gross_exposure=_as_float(gross),
            daily_pnl=_as_float(Decimal(account["realized_pnl"])),
            limits=_limits_from_row(account),
            kill_switch_active=bool(account["kill_switch"]),
            market_data_fresh=True, risk_service_available=True,
        )
    except Exception:  # noqa: BLE001 - an unavailable risk check must fail closed
        return "risk_service_unavailable"
    if not approved:
        return reason
    return _decimal_risk_reason(account, order["side"], quantity, price, position, gross)


def _decimal_risk_reason(
    account: sqlite3.Row, side: str, qty: Decimal, price: Decimal,
    position: Decimal, gross: Decimal,
) -> str | None:
    """Veto float approvals against exact ledger amounts at risk boundaries.

    Repeating average costs and realized P&L cannot round-trip through float.
    They must remain usable risk inputs without letting float rounding approve
    an amount just beyond a persisted limit.
    """
    notional = qty * price
    if notional > Decimal(account["max_order_notional"]):
        return "max_order_notional_exceeded"
    if side == "BUY":
        if position + notional > Decimal(account["max_position_notional"]):
            return "max_position_notional_exceeded"
        if Decimal(account["realized_pnl"]) <= -Decimal(account["max_daily_loss"]):
            return "daily_loss_limit_reached"
        if gross + notional > Decimal(account["max_gross_exposure"]):
            return "exposure_budget_exceeded"
    elif notional > position or notional > gross:
        return "invalid_order_or_position"
    return None


def _refresh_unrealized(conn: sqlite3.Connection) -> None:
    positions = {
        row["symbol"]: (Decimal(row["quantity"]), Decimal(row["average_cost"]))
        for row in conn.execute("SELECT symbol, quantity, average_cost FROM paper_exec_positions")
    }
    expected, _unmarked = _expected_unrealized(positions, _marks(conn))
    conn.execute(
        "UPDATE paper_exec_meta SET unrealized_pnl=? WHERE id=1", (_canonical(expected),)
    )


def _persist_application(
    conn: sqlite3.Connection, account: sqlite3.Row, symbol: str, side: str,
    qty: Decimal, px: Decimal, fee: Decimal,
) -> Decimal | None:
    held, avg = _position(conn, symbol)
    applied = _apply(Decimal(account["cash"]), held, avg, side, qty, px, fee)
    if applied is None:
        return None
    new_cash, new_qty, new_avg, realized_delta = applied
    new_realized = Decimal(account["realized_pnl"]) + realized_delta
    conn.execute(
        "UPDATE paper_exec_meta SET cash=?, realized_pnl=? WHERE id=1",
        (_canonical(new_cash), _canonical(new_realized)),
    )
    if new_qty == 0:
        conn.execute("DELETE FROM paper_exec_positions WHERE symbol=?", (symbol,))
    else:
        conn.execute(
            """INSERT INTO paper_exec_positions(symbol, quantity, average_cost)
               VALUES (?,?,?) ON CONFLICT(symbol) DO UPDATE SET
               quantity=excluded.quantity, average_cost=excluded.average_cost""",
            (symbol, _canonical(new_qty), _canonical(new_avg)),
        )
    _refresh_unrealized(conn)
    return realized_delta


def _latest_fill(conn: sqlite3.Connection, client_order_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT * FROM paper_exec_fills WHERE client_order_id=?
           ORDER BY seq DESC LIMIT 1""",
        (client_order_id,),
    ).fetchone()


def initialize_paper_account(
    initial_cash: Any,
    limits: RiskLimits | None = None,
    max_market_age_seconds: Any = "300",
    max_future_skew_seconds: Any = "60",
) -> dict[str, Any]:
    _refuse_live(False)
    limits = limits or RiskLimits()
    _validate_limits(limits)
    cash = _number(initial_cash, positive=True)
    max_age = _number(max_market_age_seconds, positive=True)
    skew = _number(max_future_skew_seconds)
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM paper_exec_meta WHERE id=1").fetchone():
                raise ValueError("account_already_initialized")
            conn.execute(
                """INSERT INTO paper_exec_meta(
                    id, initial_cash, cash, realized_pnl, kill_switch,
                    max_order_notional, max_position_notional, max_daily_loss,
                    max_gross_exposure, max_market_age_seconds, max_future_skew_seconds,
                    unrealized_pnl)
                   VALUES (1,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _canonical(cash), _canonical(cash), "0", 0,
                    _canonical(Decimal(str(limits.max_order_notional))),
                    _canonical(Decimal(str(limits.max_position_notional))),
                    _canonical(Decimal(str(limits.max_daily_loss))),
                    _canonical(Decimal(str(limits.max_gross_exposure))),
                    _canonical(max_age), _canonical(skew), "0",
                ),
            )
            _append_audit(conn, "account_initialized", None, {
                "cash": _canonical(cash), "live_execution": False, "broker_order_submitted": False,
            })
            _assert_ready(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return {"cash": _canonical(cash), "mode": "paper_only", "live_execution": False}


def set_kill_switch(active: object) -> dict[str, Any]:
    if active is not True and active is not False:
        raise ValueError("kill_switch_state_invalid")
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _assert_ready(conn)
            conn.execute("UPDATE paper_exec_meta SET kill_switch=? WHERE id=1", (1 if active else 0,))
            seq, digest = _append_audit(conn, "kill_switch_set", None, {
                "active": active is True, "live_execution": False,
            })
            _assert_ready(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return {
        "kill_switch_active": active is True, "audit_seq": seq, "audit_hash": digest,
        "live_execution": False, "broker_order_submitted": False, "mode": "paper_only",
    }


def _fresh(account: sqlite3.Row, observed_at: object, now: object) -> bool:
    observed = _parse_time(observed_at)
    current = _parse_time(now)
    if observed is None or current is None:
        return False
    age = (current - observed).total_seconds()
    max_age = float(Decimal(account["max_market_age_seconds"]))
    skew = float(Decimal(account["max_future_skew_seconds"]))
    return -skew <= age <= max_age


def _execution_reject(side: str, qty: Decimal, px: Decimal, cost: Decimal, held: Decimal) -> str:
    if side == "BUY":
        return "insufficient_paper_cash"
    if qty <= held and qty * px - cost < 0:
        return "fee_exceeds_sell_proceeds"
    return "insufficient_paper_position"


def _submit_new_order(
    conn: sqlite3.Connection, *, key: str, symbol: str, side: str,
    qty: Decimal, px: Decimal, cost: Decimal, qty_s: str, px_s: str, fee_s: str,
    observed_at: object, now: object, risk_service_available: object,
    kill_switch_active: object, working: bool, simulate_crash: bool,
) -> dict[str, Any]:
    account = conn.execute("SELECT * FROM paper_exec_meta WHERE id=1").fetchone()
    effective_kill = bool(account["kill_switch"]) or kill_switch_active is True
    fresh = _fresh(account, observed_at, now)
    filled_notional, filled_gross = _book(conn, symbol, px)
    if side == "BUY":
        extra_symbol, extra_gross = _open_buy_exposure(conn, symbol)
        position_notional = filled_notional + extra_symbol
        gross = filled_gross + extra_gross
    else:
        position_notional, gross = filled_notional, filled_gross
    try:
        approved, reason, limits_evaluated = check_execution(
            side=side, quantity=_as_float(qty), price=_as_float(px),
            current_position_notional=_as_float(position_notional),
            current_gross_exposure=_as_float(gross),
            daily_pnl=_as_float(Decimal(account["realized_pnl"])),
            limits=_limits_from_row(account),
            kill_switch_active=effective_kill, market_data_fresh=fresh,
            risk_service_available=risk_service_available is True,
        )
    except Exception:
        approved, reason, limits_evaluated = False, "risk_service_unavailable", False
    if approved:
        exact_reason = _decimal_risk_reason(account, side, qty, px, position_notional, gross)
        if exact_reason is not None:
            approved, reason = False, exact_reason
    realized_delta = Decimal("0")
    filled_quantity = Decimal("0")
    leaves_quantity = Decimal("0")
    execution_mode = "working" if working else "immediate"
    if not approved:
        status = "risk_rejected"
        state = order_transition("new", "rejected")
    else:
        reserved_cash, reserved_sell = _reserved(conn)
        held, _avg = _position(conn, symbol)
        available_cash = Decimal(account["cash"]) - reserved_cash
        available_qty = held - reserved_sell.get(symbol, Decimal("0"))
        blocked = False
        if side == "BUY" and qty * px + cost > available_cash:
            blocked = True
        elif side == "SELL" and (qty > available_qty or qty * px - cost < 0):
            blocked = True
        if blocked:
            status = "execution_rejected"
            reason = _execution_reject(side, qty, px, cost, held)
            state = order_transition("new", "rejected")
        elif working:
            status = "accepted"
            state = order_transition("new", "accepted")
            leaves_quantity = qty
        else:
            applied = _persist_application(conn, account, symbol, side, qty, px, cost)
            if applied is None:
                status = "execution_rejected"
                reason = _execution_reject(side, qty, px, cost, held)
                state = order_transition("new", "rejected")
            else:
                realized_delta = applied
                status = "filled"
                order_transition("new", "accepted")
                state = order_transition("accepted", "filled")
                filled_quantity = qty
    payload = {
        "status": status, "reason": reason, "limits_evaluated": limits_evaluated,
        "state": state, "execution_mode": execution_mode,
        "symbol": symbol, "side": side, "quantity": qty_s, "price": px_s, "fee": fee_s,
        "live_execution": False, "broker_order_submitted": False,
    }
    audit_seq, digest = _append_audit(conn, "order_decided", key, payload)
    if status == "filled":
        conn.execute(
            """INSERT INTO paper_exec_fills(
                seq, client_order_id, client_fill_id, symbol, side, quantity, price, fee, realized_pnl)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (audit_seq, key, key, symbol, side, qty_s, px_s, fee_s, _canonical(realized_delta)),
        )
        _append_audit(conn, "fill_recorded", key, {
            "fill_seq": audit_seq, "client_fill_id": key, "risk_decision_id": digest,
            "realized_pnl": _canonical(realized_delta), "quantity": qty_s, "price": px_s,
            "live_execution": False, "broker_order_submitted": False,
        })
    created = now if isinstance(now, str) else ""
    conn.execute(
        """INSERT INTO paper_exec_orders(
            client_order_id, symbol, side, quantity, price, fee, status, reason,
            limits_evaluated, risk_decision_id, created_at, state, filled_quantity,
            leaves_quantity, execution_mode)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (key, symbol, side, qty_s, px_s, fee_s, status, reason,
         1 if limits_evaluated else 0, digest, created, state,
         _canonical(filled_quantity), _canonical(leaves_quantity), execution_mode),
    )
    _assert_ready(conn)
    if simulate_crash:
        raise RuntimeError("simulated_crash_before_commit")
    conn.commit()
    stored = conn.execute(
        "SELECT * FROM paper_exec_orders WHERE client_order_id=?", (key,)
    ).fetchone()
    return _public(stored, _latest_fill(conn, key), replay=False)


def submit_paper_order(
    *,
    client_order_id: Any,
    symbol: Any,
    side: Any,
    quantity: Any,
    price: Any,
    fee: Any = "0",
    observed_at: object = None,
    now: object = None,
    risk_service_available: object = True,
    kill_switch_active: object = False,
    live_execution: object = False,
    working: bool = False,
    simulate_crash: bool = False,
) -> dict[str, Any]:
    _refuse_live(live_execution)
    if not isinstance(client_order_id, str) or not 1 <= len(client_order_id.strip()) <= 128:
        raise ValueError("invalid_client_order_id")
    if not isinstance(symbol, str) or not 1 <= len(symbol.strip()) <= 64:
        raise ValueError("invalid_symbol")
    if not isinstance(side, str):
        raise ValueError("invalid_side")
    key = client_order_id.strip()
    normalized_symbol = symbol.strip().upper()
    normalized_side = side.upper()
    if normalized_side not in ("BUY", "SELL"):
        raise ValueError("invalid_side")
    if not normalized_symbol.isascii() or any(
        ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/-" for ch in normalized_symbol
    ):
        raise ValueError("invalid_symbol")
    qty = _number(quantity, positive=True)
    px = _number(price, positive=True)
    cost = _number(fee)
    qty_s, px_s, fee_s = _canonical(qty), _canonical(px), _canonical(cost)
    conflict = False
    result: dict[str, Any] | None = None
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _assert_ready(conn)
            prior = conn.execute(
                "SELECT * FROM paper_exec_orders WHERE client_order_id=?", (key,)
            ).fetchone()
            if prior is not None:
                mode = "working" if working is True else "immediate"
                same = (
                    prior["symbol"], prior["side"], prior["quantity"], prior["price"], prior["fee"],
                    prior["execution_mode"],
                ) == (normalized_symbol, normalized_side, qty_s, px_s, fee_s, mode)
                event = "idempotent_replay" if same else "idempotency_conflict"
                _append_audit(conn, event, key, {
                    "original_risk_decision_id": prior["risk_decision_id"],
                    "original_status": prior["status"],
                    "live_execution": False,
                    "broker_order_submitted": False,
                })
                _assert_ready(conn)
                conn.commit()
                if not same:
                    conflict = True
                else:
                    result = _public(prior, _latest_fill(conn, key), replay=True)
            else:
                result = _submit_new_order(
                    conn, key=key, symbol=normalized_symbol, side=normalized_side,
                    qty=qty, px=px, cost=cost, qty_s=qty_s, px_s=px_s, fee_s=fee_s,
                    observed_at=observed_at, now=now,
                    risk_service_available=risk_service_available,
                    kill_switch_active=kill_switch_active,
                    working=working is True,
                    simulate_crash=simulate_crash,
                )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    if conflict:
        raise ValueError("idempotency_key_conflict")
    if result is None:
        raise RuntimeError("paper_order_result_missing")
    return result


def _text_id(value: Any, error: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 128:
        raise ValueError(error)
    return value.strip()


def _allocated_fee(order_fee: Decimal, leaves: Decimal, fill_qty: Decimal, charged: Decimal) -> Decimal:
    remaining = order_fee - charged
    if remaining < 0:
        raise ValueError("fill_fee_exceeds_order")
    if fill_qty == leaves:
        return remaining
    if leaves <= 0:
        raise ValueError("invalid_fill_quantity")
    return remaining * fill_qty / leaves


def _reject_fill(conn: sqlite3.Connection, key: str, event: str, payload: dict[str, Any]) -> None:
    _append_audit(conn, event, key, payload)
    _assert_ready(conn)
    conn.commit()


def apply_paper_fill(
    *,
    client_order_id: Any,
    client_fill_id: Any,
    quantity: Any,
    price: Any = None,
    fee: Any = None,
    observed_at: object = None,
    now: object = None,
    live_execution: object = False,
    simulate_crash: bool = False,
) -> dict[str, Any]:
    """Fill some or all of an accepted paper order. A crash rolls the fill back."""
    _refuse_live(live_execution)
    key = _text_id(client_order_id, "invalid_client_order_id")
    fill_id = _text_id(client_fill_id, "invalid_client_fill_id")
    fill_qty = _number(quantity, positive=True)
    explicit_price = None if price is None else _number(price, positive=True)
    explicit_fee = None if fee is None else _number(fee)
    error: str | None = None
    result: dict[str, Any] | None = None
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _assert_ready(conn)
            order = conn.execute(
                "SELECT * FROM paper_exec_orders WHERE client_order_id=?", (key,)
            ).fetchone()
            if order is None:
                raise ValueError("order_not_found")
            existing = conn.execute(
                "SELECT * FROM paper_exec_fills WHERE client_fill_id=?", (fill_id,)
            ).fetchone()
            if existing is not None:
                price_s = existing["price"] if explicit_price is None else _canonical(explicit_price)
                fee_s = existing["fee"] if explicit_fee is None else _canonical(explicit_fee)
                same = (
                    existing["client_order_id"] == key
                    and existing["quantity"] == _canonical(fill_qty)
                    and existing["price"] == price_s
                    and existing["fee"] == fee_s
                )
                event = "idempotent_replay" if same else "idempotency_conflict"
                _append_audit(conn, event, key, {
                    "client_fill_id": fill_id,
                    "original_status": order["status"],
                    "live_execution": False,
                    "broker_order_submitted": False,
                })
                _assert_ready(conn)
                conn.commit()
                if same:
                    result = _public(order, existing, replay=True)
                else:
                    error = "idempotency_key_conflict"
            else:
                account = conn.execute("SELECT * FROM paper_exec_meta WHERE id=1").fetchone()
                leaves = Decimal(order["leaves_quantity"])
                limit = Decimal(order["price"])
                fill_px = limit if explicit_price is None else explicit_price
                charged = _charged_fee(conn, key)
                try:
                    fill_fee = (
                        _allocated_fee(Decimal(order["fee"]), leaves, fill_qty, charged)
                        if explicit_fee is None else explicit_fee
                    )
                except ValueError as exc:
                    fill_fee = Decimal("0")
                    problem = str(exc)
                else:
                    problem = None
                if problem is None and explicit_fee is not None:
                    remaining_fee = Decimal(order["fee"]) - charged
                    if explicit_fee > remaining_fee:
                        problem = "fill_fee_exceeds_order"
                worse = (
                    (order["side"] == "BUY" and fill_px > limit)
                    or (order["side"] == "SELL" and fill_px < limit)
                )
                if problem is None and fill_qty > leaves:
                    problem = "invalid_fill_quantity"
                if problem is None and worse:
                    problem = "fill_price_exceeds_limit"
                if problem is None and bool(account["kill_switch"]):
                    problem = "kill_switch_active"
                if problem is None and not _fresh(account, observed_at, now):
                    problem = "stale_market_data"
                if order["state"] not in OPEN_ORDER_STATES:
                    problem = "invalid_order_transition"
                if problem is None:
                    problem = _fill_risk_reason(conn, account, order, fill_qty, fill_px)
                if problem is not None:
                    event = (
                        "invalid_order_transition"
                        if problem == "invalid_order_transition" else "fill_rejected"
                    )
                    _reject_fill(conn, key, event, {
                        "client_fill_id": fill_id, "reason": problem,
                        "state": order["state"],
                        "live_execution": False, "broker_order_submitted": False,
                    })
                    error = problem
                else:
                    try:
                        new_leaves = leaves - fill_qty
                        target = "filled" if new_leaves == 0 else "partially_filled"
                        order_transition(order["state"], target)
                    except ValueError:
                        _reject_fill(conn, key, "invalid_order_transition", {
                            "client_fill_id": fill_id,
                            "from": order["state"], "to": "filled",
                            "live_execution": False, "broker_order_submitted": False,
                        })
                        error = "invalid_order_transition"
                    else:
                        realized = _persist_application(
                            conn, account, order["symbol"], order["side"], fill_qty, fill_px, fill_fee,
                        )
                        if realized is None:
                            reason = _execution_reject(
                                order["side"], fill_qty, fill_px, fill_fee,
                                _position(conn, order["symbol"])[0],
                            )
                            _reject_fill(conn, key, "fill_rejected", {
                                "client_fill_id": fill_id, "reason": reason,
                                "state": order["state"],
                                "live_execution": False, "broker_order_submitted": False,
                            })
                            error = reason
                        else:
                            new_filled = Decimal(order["filled_quantity"]) + fill_qty
                            previous = conn.execute(
                                "SELECT seq FROM paper_exec_audit ORDER BY seq DESC LIMIT 1"
                            ).fetchone()
                            fill_seq = 1 if previous is None else int(previous["seq"]) + 1
                            qty_s = _canonical(fill_qty)
                            px_s = _canonical(fill_px)
                            fee_s = _canonical(fill_fee)
                            _append_audit(conn, "fill_recorded", key, {
                                "fill_seq": fill_seq, "client_fill_id": fill_id,
                                "risk_decision_id": order["risk_decision_id"],
                                "realized_pnl": _canonical(realized),
                                "quantity": qty_s, "price": px_s, "state": target,
                                "live_execution": False, "broker_order_submitted": False,
                            })
                            conn.execute(
                                """INSERT INTO paper_exec_fills(
                                    seq, client_order_id, client_fill_id, symbol, side,
                                    quantity, price, fee, realized_pnl)
                                   VALUES (?,?,?,?,?,?,?,?,?)""",
                                (fill_seq, key, fill_id, order["symbol"], order["side"],
                                 qty_s, px_s, fee_s, _canonical(realized)),
                            )
                            conn.execute(
                                """UPDATE paper_exec_orders
                                   SET status=?, state=?, filled_quantity=?, leaves_quantity=?
                                   WHERE client_order_id=?""",
                                (target, target, _canonical(new_filled), _canonical(new_leaves), key),
                            )
                            _assert_ready(conn)
                            if simulate_crash:
                                raise RuntimeError("simulated_crash_before_commit")
                            conn.commit()
                            stored = conn.execute(
                                "SELECT * FROM paper_exec_orders WHERE client_order_id=?", (key,)
                            ).fetchone()
                            stored_fill = conn.execute(
                                "SELECT * FROM paper_exec_fills WHERE client_fill_id=?", (fill_id,)
                            ).fetchone()
                            result = _public(stored, stored_fill, replay=False)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    if error is not None:
        raise ValueError(error)
    if result is None:
        raise RuntimeError("paper_fill_result_missing")
    return result


def cancel_paper_order(
    *,
    client_order_id: Any,
    live_execution: object = False,
    simulate_crash: bool = False,
) -> dict[str, Any]:
    """Cancel the remainder of an open paper order. A second cancel replays."""
    _refuse_live(live_execution)
    key = _text_id(client_order_id, "invalid_client_order_id")
    error: str | None = None
    result: dict[str, Any] | None = None
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _assert_ready(conn)
            order = conn.execute(
                "SELECT * FROM paper_exec_orders WHERE client_order_id=?", (key,)
            ).fetchone()
            if order is None:
                raise ValueError("order_not_found")
            if order["state"] == "cancelled":
                _append_audit(conn, "cancel_replay", key, {
                    "original_status": order["status"],
                    "filled_quantity": order["filled_quantity"],
                    "live_execution": False, "broker_order_submitted": False,
                })
                _assert_ready(conn)
                conn.commit()
                result = _public(order, _latest_fill(conn, key), replay=True)
            else:
                try:
                    order_transition(order["state"], "cancelled")
                except ValueError:
                    _append_audit(conn, "invalid_order_transition", key, {
                        "from": order["state"], "to": "cancelled",
                        "live_execution": False, "broker_order_submitted": False,
                    })
                    _assert_ready(conn)
                    conn.commit()
                    error = "invalid_order_transition"
                else:
                    conn.execute(
                        """UPDATE paper_exec_orders
                           SET status='cancelled', state='cancelled', leaves_quantity='0'
                           WHERE client_order_id=?""",
                        (key,),
                    )
                    _append_audit(conn, "order_cancelled", key, {
                        "filled_quantity": order["filled_quantity"],
                        "released_quantity": order["leaves_quantity"],
                        "live_execution": False, "broker_order_submitted": False,
                    })
                    _assert_ready(conn)
                    if simulate_crash:
                        raise RuntimeError("simulated_crash_before_commit")
                    conn.commit()
                    stored = conn.execute(
                        "SELECT * FROM paper_exec_orders WHERE client_order_id=?", (key,)
                    ).fetchone()
                    result = _public(stored, _latest_fill(conn, key), replay=False)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    if error is not None:
        raise ValueError(error)
    if result is None:
        raise RuntimeError("paper_cancel_result_missing")
    return result


def _normalize_symbol(symbol: Any) -> str:
    if not isinstance(symbol, str) or not 1 <= len(symbol.strip()) <= 64:
        raise ValueError("invalid_symbol")
    normalized = symbol.strip().upper()
    if not normalized.isascii() or any(
        ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/-" for ch in normalized
    ):
        raise ValueError("invalid_symbol")
    return normalized


def mark_to_market(marks: Any, *, observed_at: object, now: object) -> dict[str, Any]:
    """Store fresh marks and the unrealized P&L they imply. This does not trade."""
    _refuse_live(False)
    if not isinstance(marks, dict) or not marks:
        raise ValueError("invalid_marks")
    parsed: dict[str, Decimal] = {}
    for symbol, price in marks.items():
        parsed[_normalize_symbol(symbol)] = _number(price, positive=True)
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _assert_ready(conn)
            account = conn.execute("SELECT * FROM paper_exec_meta WHERE id=1").fetchone()
            if not _fresh(account, observed_at, now):
                raise ValueError("stale_market_data")
            held = {
                row["symbol"] for row in conn.execute("SELECT symbol FROM paper_exec_positions")
            }
            merged = _marks(conn)
            merged.update(parsed)
            missing = sorted(symbol for symbol in held if symbol not in merged)
            if missing:
                raise ValueError("mark_required")
            observed = observed_at if isinstance(observed_at, str) else ""
            for symbol, price in parsed.items():
                conn.execute(
                    """INSERT INTO paper_exec_marks(symbol, price, observed_at) VALUES (?,?,?)
                       ON CONFLICT(symbol) DO UPDATE SET
                       price=excluded.price, observed_at=excluded.observed_at""",
                    (symbol, _canonical(price), observed),
                )
            _refresh_unrealized(conn)
            _append_audit(conn, "mark_to_market", None, {
                "marks": {symbol: _canonical(price) for symbol, price in sorted(parsed.items())},
                "live_execution": False, "broker_order_submitted": False,
            })
            _assert_ready(conn)
            conn.commit()
            unrealized = conn.execute(
                "SELECT unrealized_pnl FROM paper_exec_meta WHERE id=1"
            ).fetchone()["unrealized_pnl"]
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return {
        "unrealized_pnl": unrealized,
        "live_execution": False, "broker_order_submitted": False, "mode": "paper_only",
    }


def _money_field(name: str, expected: Decimal, recorded: Decimal) -> dict[str, Any]:
    return {
        "field": name,
        "expected": _canonical(expected),
        "recorded": _canonical(recorded),
        "match": expected == recorded,
    }


def _build_reconciliation_report(conn: sqlite3.Connection, account: sqlite3.Row) -> dict[str, Any]:
    breaks = _reconcile_breaks(conn)
    try:
        cash, realized, positions = _recompute(conn)
        unmarked: list[str] = []
        expected_unrealized, unmarked = _expected_unrealized(positions, _marks(conn))
    except ValueError:
        cash = realized = expected_unrealized = None
        positions = {}
        unmarked = []
    recorded_positions = {
        row["symbol"]: (Decimal(row["quantity"]), Decimal(row["average_cost"]))
        for row in conn.execute("SELECT * FROM paper_exec_positions")
    }
    position_rows = []
    if cash is not None:
        for symbol in sorted(set(positions) | set(recorded_positions)):
            expected = positions.get(symbol)
            recorded = recorded_positions.get(symbol)
            position_rows.append({
                "symbol": symbol,
                "expected_quantity": None if expected is None else _canonical(expected[0]),
                "recorded_quantity": None if recorded is None else _canonical(recorded[0]),
                "expected_average_cost": None if expected is None else _canonical(expected[1]),
                "recorded_average_cost": None if recorded is None else _canonical(recorded[1]),
                "match": expected == recorded,
            })
    recorded_unrealized = Decimal(account["unrealized_pnl"])
    fields = []
    if cash is None or realized is None or expected_unrealized is None:
        mismatches = [{"field": "ledger", "expected": "recomputable", "recorded": "recompute_failed"}]
    else:
        fields = [
            _money_field("cash", cash, Decimal(account["cash"])),
            _money_field("realized_pnl", realized, Decimal(account["realized_pnl"])),
            _money_field("unrealized_pnl", expected_unrealized, recorded_unrealized),
        ]
        mismatches = [row for row in fields if not row["match"]]
        mismatches.extend(
            {"field": f"position:{row['symbol']}", "expected": row["expected_quantity"],
             "recorded": row["recorded_quantity"]}
            for row in position_rows if not row["match"]
        )
    open_orders = [
        {
            "client_order_id": row["client_order_id"], "symbol": row["symbol"],
            "side": row["side"], "state": row["state"],
            "filled_quantity": row["filled_quantity"], "leaves_quantity": row["leaves_quantity"],
        }
        for row in conn.execute(
            """SELECT * FROM paper_exec_orders
               WHERE state IN ('accepted', 'partially_filled')
               ORDER BY client_order_id"""
        )
    ]
    return {
        "initialized": True,
        "passed": not breaks,
        "breaks": breaks,
        "mismatches": mismatches,
        "fields": fields,
        "positions": position_rows,
        "warnings": [f"unmarked:{symbol}" for symbol in unmarked],
        "open_orders": open_orders,
        "daily_loss_basis": "realized_pnl",
        "live_execution": False,
        "broker_order_submitted": False,
        "mode": "paper_only",
    }


def reconciliation_report(*, record_mismatch: bool = False) -> dict[str, Any]:
    """Expected-versus-recorded cash, positions, and P&L. Mismatches can be audited."""
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            account = conn.execute("SELECT * FROM paper_exec_meta WHERE id=1").fetchone()
            if account is None:
                conn.rollback()
                return {
                    "initialized": False, "passed": False, "breaks": ["account_not_initialized"],
                    "mismatches": [{
                        "field": "account", "expected": "initialized", "recorded": None,
                    }],
                    "fields": [], "positions": [], "warnings": [], "open_orders": [],
                    "daily_loss_basis": "realized_pnl",
                    "live_execution": False, "broker_order_submitted": False, "mode": "paper_only",
                }
            try:
                _verify_audit(conn)
            except ValueError:
                conn.rollback()
                return {
                    "initialized": True, "passed": False, "breaks": ["audit_chain_corrupt"],
                    "mismatches": [{
                        "field": "audit", "expected": "intact", "recorded": "corrupt",
                    }],
                    "fields": [], "positions": [], "warnings": [], "open_orders": [],
                    "daily_loss_basis": "realized_pnl",
                    "live_execution": False, "broker_order_submitted": False, "mode": "paper_only",
                }
            report = _build_reconciliation_report(conn, account)
            if record_mismatch and report["breaks"]:
                payload = {
                    "breaks": report["breaks"],
                    "live_execution": False,
                    "broker_order_submitted": False,
                }
                encoded = _canon_json(payload)
                last = conn.execute(
                    "SELECT event, payload FROM paper_exec_audit ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                if (
                    last is None
                    or last["event"] != "reconciliation_mismatch"
                    or last["payload"] != encoded
                ):
                    _append_audit(conn, "reconciliation_mismatch", None, payload)
                conn.commit()
            else:
                conn.rollback()
            return report
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    account = conn.execute("SELECT * FROM paper_exec_meta WHERE id=1").fetchone()
    if account is None:
        return {
            "initialized": False, "mode": "paper_only", "live_execution": False,
            "broker_order_submitted": False, "fill_count": 0, "positions": [],
            "audit": [],
        }
    positions = [
        {"symbol": row["symbol"], "quantity": row["quantity"], "average_cost": row["average_cost"]}
        for row in conn.execute("SELECT * FROM paper_exec_positions ORDER BY symbol")
    ]
    audit = [dict(row) for row in conn.execute(
        "SELECT seq, client_order_id, event, payload, prev_hash, row_hash "
        "FROM paper_exec_audit ORDER BY seq"
    )]
    return {
        "initialized": True, "mode": "paper_only", "live_execution": False,
        "broker_order_submitted": False,
        "cash": account["cash"], "initial_cash": account["initial_cash"],
        "realized_pnl": account["realized_pnl"],
        "unrealized_pnl": account["unrealized_pnl"],
        "kill_switch_active": bool(account["kill_switch"]),
        "fill_count": conn.execute("SELECT COUNT(*) FROM paper_exec_fills").fetchone()[0],
        "order_count": conn.execute("SELECT COUNT(*) FROM paper_exec_orders").fetchone()[0],
        "positions": positions,
        "marks": [dict(row) for row in conn.execute(
            "SELECT symbol, price, observed_at FROM paper_exec_marks ORDER BY symbol"
        )],
        "recent_fills": [
            {**dict(row), "created_at": None}
            for row in conn.execute(
                """SELECT symbol, side, quantity, price, fee, realized_pnl
                   FROM paper_exec_fills ORDER BY seq DESC LIMIT 20"""
            )
        ],
        "orders": [
            {
                "client_order_id": row["client_order_id"], "state": row["state"],
                "status": row["status"], "symbol": row["symbol"], "side": row["side"],
                "quantity": row["quantity"], "filled_quantity": row["filled_quantity"],
                "leaves_quantity": row["leaves_quantity"],
            }
            for row in conn.execute("SELECT * FROM paper_exec_orders ORDER BY client_order_id")
        ],
        "open_orders": [
            {
                "client_order_id": row["client_order_id"],
                "symbol": row["symbol"], "side": row["side"], "state": row["state"],
                "quantity": row["quantity"], "filled_quantity": row["filled_quantity"],
                "leaves_quantity": row["leaves_quantity"], "price": row["price"],
            }
            for row in conn.execute(
                """SELECT * FROM paper_exec_orders
                   WHERE state IN ('accepted', 'partially_filled')
                   ORDER BY client_order_id"""
            )
        ],
        "audit": audit,
    }


def snapshot() -> dict[str, Any]:
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN")
            return _snapshot(conn)
        finally:
            conn.close()


def get_paper_order(client_order_id: Any) -> dict[str, Any] | None:
    """Recover a durable order outcome without deciding or submitting it again."""
    key = _text_id(client_order_id, "invalid_client_order_id")
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN")
            _assert_ready(conn)
            order = conn.execute(
                "SELECT * FROM paper_exec_orders WHERE client_order_id=?", (key,)
            ).fetchone()
            if order is None:
                return None
            result = _public(order, _latest_fill(conn, key), replay=False)
            result.update({
                name: order[name] for name in (
                    "symbol", "side", "quantity", "price", "fee", "created_at",
                )
            })
            return result
        finally:
            conn.close()


def execution_status(*, read_only: bool = False) -> dict[str, Any]:
    """Account and reconciliation from one SQLite snapshot, including across processes."""
    with _lock:
        if read_only:
            path = os.getenv("OSIRIS_PAPER_EXECUTION_DB", "")
            if not path or path == ":memory:" or not Path(path).is_file():
                return {
                    "account": {"initialized": False, "mode": "paper_only", "positions": [],
                                "fill_count": 0, "live_execution": False,
                                "broker_order_submitted": False},
                    "reconciliation": {"initialized": False, "passed": False,
                                       "breaks": ["account_not_initialized"]},
                }
            conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True,
                                   timeout=15, isolation_level=None)
            conn.row_factory = sqlite3.Row
        else:
            conn = _connect()
        try:
            conn.execute("BEGIN")
            account = conn.execute("SELECT * FROM paper_exec_meta WHERE id=1").fetchone()
            error = None
            if account is None:
                error = "account_not_initialized"
            else:
                try:
                    _verify_audit(conn)
                except ValueError:
                    error = "audit_chain_corrupt"
            report = (
                {
                    "initialized": account is not None,
                    "passed": False, "breaks": [error],
                    "fields": [], "positions": [], "warnings": [], "open_orders": [],
                    "mismatches": [], "daily_loss_basis": "realized_pnl",
                    "live_execution": False, "broker_order_submitted": False,
                    "mode": "paper_only",
                }
                if error is not None else _build_reconciliation_report(conn, account)
            )
            return {"account": _snapshot(conn), "reconciliation": report}
        finally:
            conn.close()


def account_valuation(
    account: dict[str, Any], *, now: datetime | None = None,
    max_age_seconds: float = 300, max_future_skew_seconds: float = 60,
) -> dict[str, Any]:
    """One valuation policy for every read-only view of the canonical account.

    Cash and realized P&L remain known without marks. Equity, market value and
    unrealized P&L require a fresh mark for every holding; partial totals stay null.
    Callers must reconcile the account before using this projection.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("valuation_timezone_required")
    policies = (max_age_seconds, max_future_skew_seconds)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in policies) or max_age_seconds <= 0 or max_future_skew_seconds < 0:
        raise ValueError("invalid_valuation_policy")
    marks = {row["symbol"]: row for row in account.get("marks", [])}
    market_value, times, warnings = Decimal("0"), [], []
    for position in account["positions"]:
        quantity = Decimal(position["quantity"])
        if quantity == 0:
            continue
        symbol = position["symbol"]
        if symbol not in marks:
            warnings.append(f"unmarked:{symbol}")
            continue
        marked_at = _parse_time(marks[symbol].get("observed_at"))
        if marked_at is None:
            warnings.append(f"invalid_mark_time:{symbol}")
            continue
        age = (current - marked_at).total_seconds()
        if not -max_future_skew_seconds <= age <= max_age_seconds:
            warnings.append(f"stale_mark:{symbol}")
            continue
        market_value += quantity * Decimal(marks[symbol]["price"])
        times.append(marked_at)
    complete = not warnings
    return {
        "market_value": _canonical(market_value) if complete else None,
        "pnl": {
            "realized": account["realized_pnl"],
            "unrealized": account["unrealized_pnl"] if complete else None,
            "equity": _canonical(Decimal(account["cash"]) + market_value) if complete else None,
        },
        "valuation_warnings": warnings,
        "valuation_as_of": min(times).isoformat() if times and complete else None,
        "marks_available": complete,
    }


def reconcile() -> dict[str, Any]:
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN")
            account = conn.execute("SELECT 1 FROM paper_exec_meta WHERE id=1").fetchone()
            if account is None:
                return {
                    "initialized": False, "passed": False, "breaks": ["account_not_initialized"],
                    "live_execution": False,
                }
            try:
                _verify_audit(conn)
            except ValueError:
                breaks = ["audit_chain_corrupt"]
            else:
                breaks = _reconcile_breaks(conn)
            state = {
                "initialized": True, "passed": not breaks, "breaks": breaks,
                "live_execution": False, "broker_order_submitted": False, "mode": "paper_only",
            }
            return state
        finally:
            conn.close()


def recover() -> dict[str, Any]:
    """Re-open the ledger the way a restarted process would."""
    with _lock:
        conn = _connect()
        try:
            _assert_ready(conn)
            report = {"audit_ok": True, "passed": True, "live_execution": False, "mode": "paper_only"}
        finally:
            conn.close()
    report["snapshot"] = snapshot()
    report["open_orders"] = report["snapshot"].get("open_orders", [])
    return report


def submit_approved_workflow(result: dict[str, Any], **order: Any) -> dict[str, Any]:
    """Forward only a current, controlled paper-intent proposal to the ledger.

    This is a *defense-in-depth* contract check, not authentication of an
    arbitrary dictionary. The durable paper ledger independently rechecks
    risk, freshness and its persisted kill-switch. No live order is routed.
    """
    execution = result.get("execution") if isinstance(result, dict) else None
    risk = result.get("risk") if isinstance(result, dict) else None
    intent = result.get("paper_order_intent") if isinstance(result, dict) else None
    if not isinstance(execution, dict) or execution.get("live_execution") is not False:
        raise ValueError("live_execution_disabled")
    if execution.get("broker_order_submitted") is not False:
        raise ValueError("live_execution_disabled")

    def refused(reason: str) -> dict[str, Any]:
        return {
            "status": "not_submitted", "reason": reason, "fill": None,
            "live_execution": False, "broker_order_submitted": False,
            "mode": "paper_only",
        }

    if (
        not isinstance(risk, dict)
        or risk.get("approved_for_paper") is not True
        or not isinstance(intent, dict)
        or intent.get("status") != "PAPER_INTENT_APPROVED"
    ):
        return refused(
            risk.get("reason", "risk_not_approved")
            if isinstance(risk, dict) else "risk_not_approved"
        )

    # Prior helper versions could approve without kill-switch/freshness
    # evidence. Never forward those legacy or uncontrolled approvals.
    if (
        result.get("workflow") != "research_to_paper_risk_gate"
        or execution.get("mode") != "paper_only"
        or risk.get("limits_evaluated") is not True
        or risk.get("live_execution") is not False
        or risk.get("reason") != "approved_for_simulation_only"
    ):
        return refused("approval_evidence_missing")

    def valid_quantity_or_price(actual: Any, expected: Any) -> bool:
        if isinstance(actual, bool) or isinstance(expected, bool):
            return False
        try:
            a, b = Decimal(str(actual)), Decimal(str(expected))
        except (TypeError, ValueError, InvalidOperation):
            return False
        return a.is_finite() and b.is_finite() and a > 0 and b > 0 and a == b

    expected_symbol = intent.get("symbol")
    expected_side = intent.get("side")
    selected_symbol = order.get("symbol", expected_symbol)
    selected_side = order.get("side", expected_side)
    selected_quantity = order.get("quantity", intent.get("quantity"))
    selected_price = order.get("price", intent.get("reference_price"))
    if (
        not isinstance(expected_symbol, str)
        or not isinstance(expected_side, str)
        or expected_side not in ("BUY", "SELL")
        or not isinstance(selected_symbol, str)
        or not isinstance(selected_side, str)
        or selected_symbol.strip().upper() != expected_symbol
        or selected_side.strip().upper() != expected_side
        or expected_symbol != result.get("symbol")
        or not valid_quantity_or_price(selected_quantity, intent.get("quantity"))
        or not valid_quantity_or_price(selected_price, intent.get("reference_price"))
    ):
        return refused("paper_intent_mismatch")

    raw = {
        "symbol": expected_symbol,
        "side": expected_side,
        "quantity": selected_quantity,
        "price": selected_price,
    }
    raw.update({
        key: value for key, value in order.items()
        if key not in {"symbol", "side", "quantity", "price"}
    })
    for field in ("quantity", "price", "fee"):
        if isinstance(raw.get(field), float):
            if not math.isfinite(raw[field]):
                raise ValueError("invalid_number")
            raw[field] = str(raw[field])
    return submit_paper_order(**raw)

def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/paper/execution/initialize")
def api_initialize(p: dict = Body(...)):
    limits = None
    if isinstance(p.get("limits"), dict):
        known = {
            name: p["limits"][name]
            for name in (
                "max_order_notional", "max_position_notional",
                "max_daily_loss", "max_gross_exposure",
            )
            if name in p["limits"]
        }
        limits = RiskLimits(**known) if known else RiskLimits()
    return _guard(
        initialize_paper_account, p.get("initial_cash"), limits,
        p.get("max_market_age_seconds", "300"), p.get("max_future_skew_seconds", "60"),
    )


@router.post("/paper/execution/orders")
def api_submit(p: dict = Body(...)):
    # simulate_crash is a library fault hook and is not accepted over HTTP.
    return _guard(
        submit_paper_order,
        client_order_id=p.get("client_order_id"),
        symbol=p.get("symbol"),
        side=p.get("side", ""),
        quantity=p.get("quantity"),
        price=p.get("price"),
        fee=p.get("fee", "0"),
        observed_at=p.get("observed_at"),
        now=p.get("now"),
        risk_service_available=p.get("risk_service_available", True) is True,
        kill_switch_active=p.get("kill_switch_active") is True,
        live_execution=p.get("live_execution") is True,
        working=p.get("working") is True,
    )


@router.post("/paper/execution/orders/{client_order_id}/fills")
def api_fill(client_order_id: str, p: dict = Body(...)):
    # simulate_crash is a library fault hook and is not accepted over HTTP.
    return _guard(
        apply_paper_fill,
        client_order_id=client_order_id,
        client_fill_id=p.get("client_fill_id"),
        quantity=p.get("quantity"),
        price=p.get("price"),
        fee=p.get("fee"),
        observed_at=p.get("observed_at"),
        now=p.get("now"),
        live_execution=p.get("live_execution") is True,
    )


@router.get("/paper/execution/reconciliation-report")
def api_reconciliation_report():
    return _guard(reconciliation_report)


@router.post("/paper/execution/reconciliation-report")
def api_reconciliation_report_record():
    return _guard(reconciliation_report, record_mismatch=True)


@router.post("/paper/execution/orders/{client_order_id}/cancel")
def api_cancel(client_order_id: str, p: dict | None = Body(default=None)):
    payload = p or {}
    return _guard(
        cancel_paper_order,
        client_order_id=client_order_id,
        live_execution=payload.get("live_execution") is True,
    )


@router.post("/paper/execution/marks")
def api_marks(p: dict = Body(...)):
    return _guard(
        mark_to_market, p.get("marks"), observed_at=p.get("observed_at"), now=p.get("now"),
    )


@router.post("/paper/execution/kill-switch")
def api_kill_switch(p: dict = Body(...)):
    return _guard(set_kill_switch, p.get("active"))


@router.get("/paper/execution/snapshot")
def api_snapshot():
    return _guard(snapshot)


@router.get("/paper/execution/reconcile")
def api_reconcile():
    return _guard(reconcile)


@router.post("/paper/execution/recover")
def api_recover():
    return _guard(recover)
