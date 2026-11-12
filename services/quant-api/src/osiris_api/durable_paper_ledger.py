"""SQLite-backed, idempotent synthetic fills; no broker connectivity.

Paper-only accounting uses Decimal and a single serialized write transaction.
Long-only, fully funded BUY/SELL; mark-to-market is explicitly unrealized.
"""
from __future__ import annotations

import os
import re
import sqlite3
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from .decimal_safety import bounded_decimal

router = APIRouter()
_lock = Lock()


def _number(value: Any, *, positive: bool = False) -> Decimal:
    number = bounded_decimal(value, allow_float=True)
    if (positive and number <= 0) or (not positive and number < 0):
        raise ValueError("invalid_number")
    return number


def _database() -> str:
    path = os.getenv("OSIRIS_PAPER_LEDGER_DB") or os.getenv(
        "OSIRIS_DB_PATH", "data/osiris_research.sqlite3"
    )
    if path == ":memory:":
        raise ValueError("paper_ledger_requires_durable_file")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_database(), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_account (
        id INTEGER PRIMARY KEY CHECK(id=1), cash TEXT NOT NULL,
        realized_pnl TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_positions (
        symbol TEXT PRIMARY KEY, quantity TEXT NOT NULL, average_cost TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_fills (
        key TEXT PRIMARY KEY, symbol TEXT NOT NULL, side TEXT NOT NULL,
        quantity TEXT NOT NULL, price TEXT NOT NULL, fee TEXT NOT NULL,
        realized_pnl TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit()
    return conn


def initialize_account(initial_cash: Any) -> dict:
    cash = _number(initial_cash, positive=True)
    with _lock, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM paper_account WHERE id=1").fetchone():
            raise ValueError("account_already_initialized")
        conn.execute("INSERT INTO paper_account VALUES (1, ?, '0')", (str(cash),))
    return {"cash": str(cash), "mode": "synthetic-paper-only"}


def synthetic_fill(key: str, symbol: str, side: str, quantity: Any, price: Any,
                   fee: Any = "0") -> dict:
    if not isinstance(key, str) or not 1 <= len(key.strip()) <= 128:
        raise ValueError("invalid_idempotency_key")
    if not isinstance(symbol, str) or not 1 <= len(symbol.strip()) <= 64:
        raise ValueError("invalid_symbol")
    if not isinstance(side, str):
        raise ValueError("invalid_side")
    key, symbol, side = key.strip(), symbol.strip().upper(), side.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError("invalid_side")
    qty, px, cost = _number(quantity, positive=True), _number(price, positive=True), _number(fee)
    if not symbol.isascii() or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/-" for ch in symbol):
        raise ValueError("invalid_symbol")
    with _lock, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        prior = conn.execute("SELECT * FROM paper_fills WHERE key=?", (key,)).fetchone()
        if prior:
            if (prior["symbol"], prior["side"], prior["quantity"], prior["price"], prior["fee"]) != (
                symbol, side, str(qty), str(px), str(cost)
            ):
                raise ValueError("idempotency_key_conflict")
            return {"key": key, "status": "idempotent_replay", "realized_pnl": prior["realized_pnl"]}
        account = conn.execute("SELECT * FROM paper_account WHERE id=1").fetchone()
        if not account:
            raise ValueError("account_not_initialized")
        cash, realized = Decimal(account["cash"]), Decimal(account["realized_pnl"])
        position = conn.execute("SELECT * FROM paper_positions WHERE symbol=?", (symbol,)).fetchone()
        held = Decimal(position["quantity"]) if position else Decimal("0")
        avg = Decimal(position["average_cost"]) if position else Decimal("0")
        if side == "BUY":
            total = qty * px + cost
            if total > cash:
                raise ValueError("insufficient_paper_cash")
            new_qty = held + qty
            new_avg = (held * avg + qty * px + cost) / new_qty
            cash -= total
            delta = Decimal("0")
        else:
            if qty > held:
                raise ValueError("insufficient_paper_position")
            proceeds = qty * px - cost
            if proceeds < 0:
                raise ValueError("fee_exceeds_sell_proceeds")
            cash += proceeds
            delta = qty * (px - avg) - cost
            realized += delta
            new_qty, new_avg = held - qty, avg
        if new_qty:
            conn.execute("""INSERT INTO paper_positions(symbol,quantity,average_cost)
                VALUES (?,?,?) ON CONFLICT(symbol) DO UPDATE SET
                quantity=excluded.quantity, average_cost=excluded.average_cost""",
                (symbol, str(new_qty), str(new_avg)))
        else:
            conn.execute("DELETE FROM paper_positions WHERE symbol=?", (symbol,))
        conn.execute("UPDATE paper_account SET cash=?,realized_pnl=? WHERE id=1",
                     (str(cash), str(realized)))
        conn.execute("""INSERT INTO paper_fills(key,symbol,side,quantity,price,fee,realized_pnl)
            VALUES(?,?,?,?,?,?,?)""", (key, symbol, side, str(qty), str(px), str(cost), str(delta)))
    return {"key": key, "status": "synthetic_fill", "realized_pnl": str(delta)}


_FILL_AMOUNT = re.compile(r"^-?\d+(?:\.\d+)?$")
_FILL_TIME = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
_RECENT_FILL_LIMIT = 20


def _recent_fills(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Newest synthetic fills. Idempotency keys are omitted on purpose."""
    rows = conn.execute(
        """SELECT symbol, side, quantity, price, fee, realized_pnl, created_at
           FROM paper_fills ORDER BY rowid DESC LIMIT ?""",
        (_RECENT_FILL_LIMIT,),
    )
    fills: list[dict[str, Any]] = []
    for row in rows:
        symbol, side = str(row["symbol"]), str(row["side"])
        amounts = (row["quantity"], row["price"], row["fee"], row["realized_pnl"])
        if side not in {"BUY", "SELL"}:
            continue
        if not symbol.isascii() or any(
            ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/-" for ch in symbol
        ):
            continue
        if any(
            not isinstance(value, str) or _FILL_AMOUNT.fullmatch(value) is None
            for value in amounts
        ):
            continue
        created = row["created_at"]
        fills.append({
            "symbol": symbol,
            "side": side,
            "quantity": row["quantity"],
            "price": row["price"],
            "fee": row["fee"],
            "realized_pnl": row["realized_pnl"],
            "created_at": (
                created.replace(" ", "T") + "Z"
                if isinstance(created, str) and _FILL_TIME.fullmatch(created) else None
            ),
        })
    return fills


def snapshot(marks: dict[str, Any] | None = None) -> dict:
    marks = marks or {}
    with _connect() as conn:
        recent = _recent_fills(conn)
        account = conn.execute("SELECT * FROM paper_account WHERE id=1").fetchone()
        if not account:
            return {
                "initialized": False, "mode": "synthetic-paper-only", "positions": [],
                "cash": None, "pnl": None, "recent_fills": recent, "market_value": None,
            }
        positions, unrealized, market_value = [], Decimal("0"), Decimal("0")
        for row in conn.execute("SELECT * FROM paper_positions ORDER BY symbol"):
            qty, avg = Decimal(row["quantity"]), Decimal(row["average_cost"])
            mark = _number(marks[row["symbol"]], positive=True) if row["symbol"] in marks else None
            if mark is not None:
                unrealized += qty * (mark - avg)
                market_value += qty * mark
            positions.append({"symbol": row["symbol"], "quantity": str(qty),
                              "average_cost": str(avg), "mark": str(mark) if mark is not None else None})
        count = conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0]
        marks_complete = all(position["mark"] is not None for position in positions)
        return {"initialized": True, "mode": "synthetic-paper-only", "cash": account["cash"],
                "positions": positions, "fill_count": count, "recent_fills": recent,
                "market_value": str(market_value) if marks_complete else None,
                "pnl": {"realized": account["realized_pnl"],
                        "unrealized": str(unrealized) if marks_complete else None,
                        "equity": str(Decimal(account["cash"]) + market_value)
                                  if marks_complete else None}}


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/paper/ledger/initialize")
def api_initialize(p: dict = Body(...)):
    return _guard(initialize_account, p.get("initial_cash"))


@router.post("/paper/ledger/synthetic-fill")
def api_synthetic_fill(p: dict = Body(...)):
    if os.getenv("OSIRIS_ENABLE_SYNTHETIC_FILLS") != "1":
        raise HTTPException(status_code=403, detail="synthetic_fills_disabled")
    return _guard(synthetic_fill, p.get("key"), p.get("symbol"), p.get("side", ""),
                  p.get("quantity"), p.get("price"), p.get("fee", "0"))


@router.get("/paper/ledger/snapshot")
def api_snapshot():
    return _guard(snapshot)
