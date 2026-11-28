"""Apply master corporate actions to the canonical paper book.

Economics come from the instrument master. A caller cannot supply a ratio or a
dividend. A split or cash dividend is applied once, in effective-time order,
and the journal replay must reproduce it. Symbol changes are reported and not
applied. Live trading stays disabled.
"""
from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from typing import Any

from .instrument_master import InstrumentMasterStore, instrument_db_path
from . import paper_execution as ledger

_SUPPORTED = frozenset({"split", "cash_dividend"})


def apply_paper_corporate_actions(as_of: object, *, simulate_crash: bool = False) -> dict[str, Any]:
    """Apply due master actions. Skipped actions do not change the book."""
    ledger._refuse_live(False)
    if type(simulate_crash) is not bool:
        raise ValueError("working_state_invalid")
    when = ledger._parse_time(as_of)
    if when is None or not isinstance(as_of, str):
        raise ValueError("invalid_corporate_action_time")
    store = InstrumentMasterStore(instrument_db_path())
    store.ensure_seeded()
    due, not_due = [], 0
    for action in store.list_corporate_actions():
        effective = ledger._parse_time(action.get("effective_at"))
        if effective is None:
            raise ValueError("invalid_corporate_action_time")
        if effective <= when:
            due.append(action)
        else:
            not_due += 1
    due.sort(key=lambda row: (
        row["effective_at"], row["instrument_id"], row["action_type"], row["content_hash"],
    ))
    applied: list[dict[str, Any]] = []
    unchanged: list[str] = []
    skipped: list[dict[str, str]] = []
    with ledger._lock:
        conn = ledger._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            ledger._assert_ready(conn)
            done = {
                row["content_hash"]
                for row in conn.execute("SELECT content_hash FROM paper_exec_corporate_applications")
            }
            for action in due:
                if action["content_hash"] in done:
                    unchanged.append(action["content_hash"])
                    continue
                reason = _apply_one(conn, store, action)
                if reason is None:
                    applied.append({
                        "content_hash": action["content_hash"],
                        "instrument_id": action["instrument_id"],
                        "action_type": action["action_type"],
                        "effective_at": action["effective_at"],
                    })
                    done.add(action["content_hash"])
                else:
                    skipped.append({
                        "content_hash": action["content_hash"],
                        "instrument_id": action["instrument_id"],
                        "reason": reason,
                    })
            ledger._assert_ready(conn)
            if simulate_crash:
                raise RuntimeError("simulated_crash_before_commit")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return {
        "as_of": when.isoformat(),
        "applied": applied,
        "unchanged": unchanged,
        "skipped": skipped,
        "not_due": not_due,
        "live_execution": False,
        "broker_order_submitted": False,
        "mode": "paper_only",
    }


def _apply_one(conn, store: InstrumentMasterStore, action: dict[str, Any]) -> str | None:
    """Return a skip reason, or None after writing one exact application."""
    if action.get("action_type") not in _SUPPORTED:
        return "unsupported_corporate_action"
    instrument = store.get(action["instrument_id"], as_of=action["effective_at"])
    if instrument is None:
        return "instrument_not_in_master"
    symbol = instrument["instrument"]["symbol"]
    decision = store.assess(symbol, as_of=action["effective_at"])
    if decision["reason"] == "ambiguous_instrument" or decision["instrument_id"] != action["instrument_id"]:
        return "ambiguous_instrument"
    if conn.execute(
        """SELECT 1 FROM paper_exec_orders
           WHERE symbol=? AND state IN ('accepted', 'partially_filled')""",
        (symbol,),
    ).fetchone() is not None:
        return "open_order_blocks_corporate_action"
    if conn.execute(
        "SELECT 1 FROM paper_exec_marks WHERE symbol=?", (symbol,),
    ).fetchone() is not None:
        return "mark_blocks_corporate_action"
    held, average = ledger._position(conn, symbol)
    if held > 0 and not _position_predates(conn, symbol, action["effective_at"]):
        return "position_crosses_effective_time"
    try:
        effect = _effect(action, held, average)
    except (ValueError, ArithmeticError):
        return "corporate_action_not_exact"
    account = conn.execute("SELECT * FROM paper_exec_meta WHERE id=1").fetchone()
    credit = Decimal(effect["cash_delta"])
    if credit != 0:
        cash = ledger._finite_decimal(Fraction(account["cash"]) + Fraction(credit))
        realized = ledger._finite_decimal(Fraction(account["realized_pnl"]) + Fraction(credit))
        conn.execute(
            "UPDATE paper_exec_meta SET cash=?, realized_pnl=? WHERE id=1",
            (ledger._canonical(cash), ledger._canonical(realized)),
        )
    new_qty = Decimal(effect["quantity_after"])
    if new_qty == 0:
        conn.execute("DELETE FROM paper_exec_positions WHERE symbol=?", (symbol,))
    else:
        conn.execute(
            """INSERT INTO paper_exec_positions(symbol, quantity, average_cost)
               VALUES (?,?,?) ON CONFLICT(symbol) DO UPDATE SET
               quantity=excluded.quantity, average_cost=excluded.average_cost""",
            (symbol, effect["quantity_after"], effect["average_cost_after"]),
        )
    ledger._refresh_unrealized(conn)
    payload = {
        "content_hash": action["content_hash"],
        "instrument_id": action["instrument_id"],
        "symbol": symbol,
        "action_type": action["action_type"],
        "effective_at": action["effective_at"],
        **effect,
        "live_execution": False,
        "broker_order_submitted": False,
    }
    seq, _digest = ledger._append_audit(conn, "corporate_action_applied", None, payload)
    conn.execute(
        """INSERT INTO paper_exec_corporate_applications(
            content_hash, audit_seq, symbol, action_type, effective_at, payload)
           VALUES (?,?,?,?,?,?)""",
        (action["content_hash"], seq, symbol, action["action_type"],
         action["effective_at"], ledger._canon_json(payload)),
    )
    return None


def _position_predates(conn, symbol: str, effective_at: str) -> bool:
    effective = ledger._parse_time(effective_at)
    if effective is None:
        return False
    rows = conn.execute(
        """SELECT created_at FROM paper_exec_orders
           WHERE symbol=? AND state!='rejected'""",
        (symbol,),
    ).fetchall()
    if not rows:
        return False
    for row in rows:
        created = ledger._parse_time(row["created_at"])
        if created is None or created >= effective:
            return False
    return True


def _effect(action: dict[str, Any], qty: Decimal, average: Decimal) -> dict[str, str]:
    before_qty, before_avg = ledger._canonical(qty), ledger._canonical(average)
    if action["action_type"] == "split":
        ratio = Decimal(action["ratio"])
        if ratio <= 0:
            raise ValueError("corporate_action_not_exact")
        if qty == 0:
            new_qty = new_avg = Decimal("0")
        else:
            new_qty = ledger._finite_decimal(Fraction(qty) * Fraction(ratio))
            new_avg = ledger._finite_decimal(Fraction(average) / Fraction(ratio))
            if Fraction(qty) * Fraction(average) != Fraction(new_qty) * Fraction(new_avg):
                raise ValueError("corporate_action_not_exact")
        return {
            "quantity_before": before_qty,
            "quantity_after": ledger._canonical(new_qty),
            "average_cost_before": before_avg,
            "average_cost_after": ledger._canonical(new_avg),
            "cash_delta": "0",
            "ratio": ledger._canonical(ratio),
        }
    per_share = Decimal(action["cash_per_share"])
    if per_share < 0:
        raise ValueError("corporate_action_not_exact")
    credit = ledger._finite_decimal(Fraction(qty) * Fraction(per_share))
    return {
        "quantity_before": before_qty,
        "quantity_after": before_qty,
        "average_cost_before": before_avg,
        "average_cost_after": before_avg,
        "cash_delta": ledger._canonical(credit),
        "cash_per_share": ledger._canonical(per_share),
    }
