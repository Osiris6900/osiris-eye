"""Durable paper execution. Live broker routing is disabled and not configurable.

Orders are risk-gated, idempotent, and stored in SQLite. A crash before commit
leaves no partial fill. Restart reads the same cash, positions, P&L, and audit
chain. This is a simulation ledger, not a production trading system.
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

router = APIRouter()
_lock = Lock()


def _refuse_live(live_execution: object) -> None:
    if (
        LIVE_EXECUTION_ENABLED is not False
        or live_execution is True
        or os.getenv("OSIRIS_ENABLE_LIVE_EXECUTION")
    ):
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
    if not value == value or value in (float("inf"), float("-inf")):  # NaN
        raise ValueError("invalid_numeric_input")
    if Decimal(str(value)) != number:
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
        max_market_age_seconds TEXT NOT NULL, max_future_skew_seconds TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_exec_positions (
        symbol TEXT PRIMARY KEY, quantity TEXT NOT NULL, average_cost TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_exec_orders (
        client_order_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, side TEXT NOT NULL,
        quantity TEXT NOT NULL, price TEXT NOT NULL, fee TEXT NOT NULL,
        status TEXT NOT NULL, reason TEXT NOT NULL, limits_evaluated INTEGER NOT NULL,
        risk_decision_id TEXT NOT NULL, created_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_exec_fills (
        seq INTEGER PRIMARY KEY, client_order_id TEXT NOT NULL UNIQUE,
        symbol TEXT NOT NULL, side TEXT NOT NULL, quantity TEXT NOT NULL,
        price TEXT NOT NULL, fee TEXT NOT NULL, realized_pnl TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_exec_audit (
        seq INTEGER PRIMARY KEY, client_order_id TEXT, event TEXT NOT NULL,
        payload TEXT NOT NULL, prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL)""")
    return conn


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
    for row in conn.execute("SELECT * FROM paper_exec_positions"):
        qty = Decimal(row["quantity"])
        mark = price if row["symbol"] == symbol else Decimal(row["average_cost"])
        gross += abs(qty * mark)
        if row["symbol"] == symbol:
            symbol_notional = qty * price
    return symbol_notional, gross


def _public(order: sqlite3.Row, fill: sqlite3.Row | None, *, replay: bool) -> dict[str, Any]:
    body = {
        "client_order_id": order["client_order_id"],
        "status": "idempotent_replay" if replay else order["status"],
        "original_status": order["status"],
        "reason": order["reason"],
        "limits_evaluated": bool(order["limits_evaluated"]),
        "risk_decision_id": order["risk_decision_id"],
        "fill": None if fill is None else {
            "seq": int(fill["seq"]),
            "quantity": fill["quantity"],
            "price": fill["price"],
            "realized_pnl": fill["realized_pnl"],
        },
        "live_execution": False,
        "broker_order_submitted": False,
        "mode": "paper_only",
    }
    return body


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
                """INSERT INTO paper_exec_meta VALUES (1,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _canonical(cash), _canonical(cash), "0", 0,
                    _canonical(Decimal(str(limits.max_order_notional))),
                    _canonical(Decimal(str(limits.max_position_notional))),
                    _canonical(Decimal(str(limits.max_daily_loss))),
                    _canonical(Decimal(str(limits.max_gross_exposure))),
                    _canonical(max_age), _canonical(skew),
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
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _assert_ready(conn)
            prior = conn.execute(
                "SELECT * FROM paper_exec_orders WHERE client_order_id=?", (key,)
            ).fetchone()
            if prior is not None:
                same = (
                    prior["symbol"], prior["side"], prior["quantity"], prior["price"], prior["fee"],
                ) == (normalized_symbol, normalized_side, qty_s, px_s, fee_s)
                if not same:
                    raise ValueError("idempotency_key_conflict")
                fill = conn.execute(
                    "SELECT * FROM paper_exec_fills WHERE client_order_id=?", (key,)
                ).fetchone()
                result = _public(prior, fill, replay=True)
                conn.commit()
                return result
            account = conn.execute("SELECT * FROM paper_exec_meta WHERE id=1").fetchone()
            effective_kill = bool(account["kill_switch"]) or kill_switch_active is True
            fresh = _fresh(account, observed_at, now)
            position_notional, gross = _book(conn, normalized_symbol, px)
            try:
                approved, reason, limits_evaluated = check_execution(
                    side=normalized_side,
                    quantity=_as_float(qty),
                    price=_as_float(px),
                    current_position_notional=_as_float(position_notional),
                    current_gross_exposure=_as_float(gross),
                    daily_pnl=_as_float(Decimal(account["realized_pnl"])),
                    limits=_limits_from_row(account),
                    kill_switch_active=effective_kill,
                    market_data_fresh=fresh,
                    risk_service_available=risk_service_available is True,
                )
            except Exception:
                approved, reason, limits_evaluated = False, "risk_service_unavailable", False
            status = "filled" if approved else "risk_rejected"
            realized_delta = Decimal("0")
            fill_seq = None
            if approved:
                cash = Decimal(account["cash"])
                row = conn.execute(
                    "SELECT * FROM paper_exec_positions WHERE symbol=?", (normalized_symbol,)
                ).fetchone()
                held = Decimal(row["quantity"]) if row else Decimal("0")
                avg = Decimal(row["average_cost"]) if row else Decimal("0")
                applied = _apply(cash, held, avg, normalized_side, qty, px, cost)
                if applied is None:
                    status = "execution_rejected"
                    reason = (
                        "insufficient_paper_cash" if normalized_side == "BUY"
                        else "insufficient_paper_position"
                    )
                    if normalized_side == "SELL" and qty <= held and qty * px - cost < 0:
                        reason = "fee_exceeds_sell_proceeds"
                else:
                    new_cash, new_qty, new_avg, realized_delta = applied
                    new_realized = Decimal(account["realized_pnl"]) + realized_delta
                    conn.execute(
                        "UPDATE paper_exec_meta SET cash=?, realized_pnl=? WHERE id=1",
                        (_canonical(new_cash), _canonical(new_realized)),
                    )
                    if new_qty == 0:
                        conn.execute(
                            "DELETE FROM paper_exec_positions WHERE symbol=?", (normalized_symbol,)
                        )
                    else:
                        conn.execute(
                            """INSERT INTO paper_exec_positions(symbol, quantity, average_cost)
                               VALUES (?,?,?) ON CONFLICT(symbol) DO UPDATE SET
                               quantity=excluded.quantity, average_cost=excluded.average_cost""",
                            (normalized_symbol, _canonical(new_qty), _canonical(new_avg)),
                        )
            payload = {
                "status": status, "reason": reason, "limits_evaluated": limits_evaluated,
                "symbol": normalized_symbol, "side": normalized_side,
                "quantity": qty_s, "price": px_s, "fee": fee_s,
                "live_execution": False, "broker_order_submitted": False,
            }
            audit_seq, digest = _append_audit(conn, "order_decided", key, payload)
            if status == "filled":
                fill_seq = audit_seq
                conn.execute(
                    """INSERT INTO paper_exec_fills(
                        seq, client_order_id, symbol, side, quantity, price, fee, realized_pnl)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (fill_seq, key, normalized_symbol, normalized_side, qty_s, px_s, fee_s,
                     _canonical(realized_delta)),
                )
            created = now if isinstance(now, str) else ""
            conn.execute(
                """INSERT INTO paper_exec_orders(
                    client_order_id, symbol, side, quantity, price, fee, status, reason,
                    limits_evaluated, risk_decision_id, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (key, normalized_symbol, normalized_side, qty_s, px_s, fee_s, status, reason,
                 1 if limits_evaluated else 0, digest, created),
            )
            _assert_ready(conn)
            if simulate_crash:
                raise RuntimeError("simulated_crash_before_commit")
            conn.commit()
            stored = conn.execute(
                "SELECT * FROM paper_exec_orders WHERE client_order_id=?", (key,)
            ).fetchone()
            stored_fill = conn.execute(
                "SELECT * FROM paper_exec_fills WHERE client_order_id=?", (key,)
            ).fetchone()
            return _public(stored, stored_fill, replay=False)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def snapshot() -> dict[str, Any]:
    with _lock:
        conn = _connect()
        try:
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
                "kill_switch_active": bool(account["kill_switch"]),
                "fill_count": conn.execute("SELECT COUNT(*) FROM paper_exec_fills").fetchone()[0],
                "order_count": conn.execute("SELECT COUNT(*) FROM paper_exec_orders").fetchone()[0],
                "positions": positions, "audit": audit,
            }
        finally:
            conn.close()


def reconcile() -> dict[str, Any]:
    with _lock:
        conn = _connect()
        try:
            account = conn.execute("SELECT 1 FROM paper_exec_meta WHERE id=1").fetchone()
            if account is None:
                return {
                    "initialized": False, "passed": False, "breaks": ["account_not_initialized"],
                    "live_execution": False,
                }
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
    return report


def submit_approved_workflow(result: dict[str, Any], **order: Any) -> dict[str, Any]:
    """Submit only a workflow result that this process already approved.

    A blocked intent never opens the ledger. Live flags are refused.
    """
    execution = result.get("execution") if isinstance(result, dict) else None
    risk = result.get("risk") if isinstance(result, dict) else None
    intent = result.get("paper_order_intent") if isinstance(result, dict) else None
    if not isinstance(execution, dict) or execution.get("live_execution") is not False:
        raise ValueError("live_execution_disabled")
    if execution.get("broker_order_submitted") is not False:
        raise ValueError("live_execution_disabled")
    if (
        not isinstance(risk, dict)
        or risk.get("approved_for_paper") is not True
        or risk.get("live_execution") not in (False, None)
        or not isinstance(intent, dict)
        or intent.get("status") != "PAPER_INTENT_APPROVED"
    ):
        return {
            "status": "not_submitted",
            "reason": risk.get("reason") if isinstance(risk, dict) else "risk_not_approved",
            "fill": None,
            "live_execution": False,
            "broker_order_submitted": False,
            "mode": "paper_only",
        }
    raw = {
        "symbol": order.get("symbol", intent.get("symbol")),
        "side": order.get("side", intent.get("side")),
        "quantity": order.get("quantity", intent.get("quantity")),
        "price": order.get("price", intent.get("reference_price")),
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
