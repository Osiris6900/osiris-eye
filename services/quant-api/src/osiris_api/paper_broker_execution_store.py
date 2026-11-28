"""Retain broker execution evidence without authorizing financial accounting.

Only the adapter's fresh GET collection calls the private writer. Provider
activity IDs are preserved; absent fee evidence remains unknown. These rows
never create fills, alter cash or positions, or clear the filled-risk hold.
"""

from __future__ import annotations

import json
import re
import sqlite3
from fractions import Fraction

from .control_plane.database import canonical, digest, identifier
from .paper_broker_reservations import broker_binding

SCHEMA = "osiris.paper-broker-execution-evidence.v1"
EVENT = "broker_execution_evidence_recorded"
ORIGIN = "https://paper-api.alpaca.markets"
ACTIVITY_FIELDS = frozenset({
    "id", "order_id", "activity_type", "type", "symbol", "side", "qty", "price",
    "cum_qty", "leaves_qty", "transaction_time",
})


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_exec_broker_execution_evidence (
        client_order_id TEXT PRIMARY KEY, body TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_exec_broker_execution_activities (
        namespace TEXT NOT NULL, activity_id TEXT NOT NULL, body TEXT NOT NULL,
        PRIMARY KEY(namespace, activity_id))""")


def _provider_id(value: object) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 256
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("broker_execution_evidence_invalid")
    return value


def _material(bundle: dict) -> dict:
    return {key: value for key, value in bundle.items()
            if key not in {"retrieved_at", "control_version"}}


def _record(control_db: str, bundle: object) -> dict:
    from . import paper_execution as ledger
    from .control_plane.broker import money, validate_broker_snapshot

    try:
        bundle = json.loads(canonical(bundle))
        if not isinstance(bundle, dict) or (
            bundle.get("schema") != SCHEMA or bundle.get("origin") != ORIGIN
            or bundle.get("status") != "complete" or bundle.get("fee_status") != "unknown"
            or bundle.get("accounting_authorized") is not False
            or bundle.get("live_execution") is not False
            or type(bundle.get("control_version")) is not int or bundle["control_version"] < 1
            or ledger._parse_time(bundle.get("retrieved_at")) is None
        ):
            raise ValueError()
        order = bundle["order"]
        key = identifier(bundle["client_order_id"])
        if key != order["client_order_id"] or bundle["binding"] != broker_binding(control_db, order):
            raise ValueError()
        before, after = bundle["before_snapshot"], bundle["after_snapshot"]
        validate_broker_snapshot(order, before)
        validate_broker_snapshot(order, after, previous=before)
        before_average = (Fraction(0) if before.get("filled_avg_price") is None
                          else Fraction(money(before["filled_avg_price"], positive=False)))
        after_average = (Fraction(0) if after.get("filled_avg_price") is None
                         else Fraction(money(after["filled_avg_price"], positive=False)))
        if (bundle["broker_order_id"] != after["id"] or before["status"] != after["status"]
                or money(before["filled_qty"], positive=False) != money(after["filled_qty"], positive=False)
                or before_average != after_average):
            raise ValueError()
        activities, hashes = bundle["activities"], bundle["raw_page_hashes"]
        if (not isinstance(activities, list) or len(activities) > 1000
                or not isinstance(hashes, list) or not 1 <= len(hashes) <= 10
                or any(not isinstance(item, str) or re.fullmatch(r"[0-9a-f]{64}", item) is None
                       for item in hashes)):
            raise ValueError()
        total, notional, seen = Fraction(0), Fraction(0), set()
        previous_time = None
        for activity in activities:
            if not isinstance(activity, dict) or set(activity) != ACTIVITY_FIELDS:
                raise ValueError()
            activity_id = _provider_id(activity["id"])
            if activity_id in seen:
                raise ValueError()
            seen.add(activity_id)
            qty, price = money(activity["qty"]), money(activity["price"])
            stamp = ledger._parse_time(activity["transaction_time"])
            total += Fraction(qty)
            notional += Fraction(qty) * Fraction(price)
            if (activity["order_id"] != after["id"] or activity["symbol"] != order["symbol"]
                    or activity["side"] != order["side"].lower()
                    or activity["activity_type"] != "FILL"
                    or activity["type"] not in {"fill", "partial_fill"}
                    or total != Fraction(money(activity["cum_qty"], positive=False))
                    or Fraction(money(activity["leaves_qty"], positive=False))
                    != Fraction(money(order["quantity"])) - total
                    or stamp is None or (previous_time is not None and stamp < previous_time)
                    or (order["side"].upper() == "BUY" and price > money(order["limit_price"]))
                    or (order["side"].upper() == "SELL" and price < money(order["limit_price"]))):
                raise ValueError()
            previous_time = stamp
        filled = money(after["filled_qty"], positive=False)
        if total != Fraction(filled) or (filled and notional != Fraction(filled) * Fraction(
            money(after["filled_avg_price"])
        )):
            raise ValueError()
        return {
            "control_db": control_db,
            "namespace": digest({"origin": ORIGIN, "control_db": control_db}),
            "bundle": bundle, "evidence_hash": digest(_material(bundle)),
        }
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, ArithmeticError):
        raise ValueError("broker_execution_evidence_invalid") from None


def _compatible(previous: dict, record: dict) -> None:
    if previous["namespace"] != record["namespace"] or (
        previous["bundle"]["binding"] != record["bundle"]["binding"]
    ):
        raise ValueError("broker_execution_evidence_conflict")
    current = {item["id"]: item for item in record["bundle"]["activities"]}
    for activity in previous["bundle"]["activities"]:
        if current.get(activity["id"]) != activity:
            raise ValueError("broker_execution_evidence_conflict")


def _receipt(record: dict, *, replay: bool) -> dict:
    bundle = record["bundle"]
    return {
        "status": "evidence_recorded", "schema": SCHEMA,
        "client_order_id": bundle["client_order_id"],
        "broker_order_id": bundle["broker_order_id"],
        "activity_count": len(bundle["activities"]),
        "evidence_hash": record["evidence_hash"], "retrieved_at": bundle["retrieved_at"],
        "replayed": replay, "fee_status": "unknown", "accounting_authorized": False,
        "financial_state_changed": False, "live_execution": False,
    }


def _persist_verified_execution_evidence(control_db: str, bundle: dict) -> dict:
    """Internal adapter writer; input is collected from fresh fixed-origin GETs.

    The control-store CAS has already succeeded. A canonical failure leaves
    earlier commitments held and requires reconciliation; it never invents a
    financial fill or acknowledges missing fees.
    """
    from . import paper_broker_reservations as reservations
    from . import paper_execution as ledger

    record = _record(control_db, bundle)
    bundle = record["bundle"]
    key = bundle["client_order_id"]
    with ledger._lock:
        conn = ledger._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            ledger._assert_ready(conn, allow_broker_break=True)
            reservation = reservations.read(conn, key)
            if (reservation is None or reservation["binding"] != bundle["binding"]
                    or reservation["order"] != bundle["order"]):
                raise ValueError("broker_reservation_required")
            prior_row = conn.execute(
                "SELECT body FROM paper_exec_broker_execution_evidence WHERE client_order_id=?", (key,),
            ).fetchone()
            previous = json.loads(prior_row[0]) if prior_row is not None else None
            if previous is not None:
                _compatible(previous, record)
            for activity in bundle["activities"]:
                stored = conn.execute(
                    "SELECT body FROM paper_exec_broker_execution_activities "
                    "WHERE namespace=? AND activity_id=?", (record["namespace"], activity["id"]),
                ).fetchone()
                body = {"binding": bundle["binding"], "activity": activity}
                if stored is not None and json.loads(stored[0]) != body:
                    raise ValueError("broker_execution_evidence_conflict")
            reservations.update(conn, key, bundle["binding"], bundle["order"], bundle["after_snapshot"])
            replay = previous is not None and previous["evidence_hash"] == record["evidence_hash"]
            if not replay:
                conn.execute(
                    "INSERT INTO paper_exec_broker_execution_evidence VALUES (?,?) "
                    "ON CONFLICT(client_order_id) DO UPDATE SET body=excluded.body",
                    (key, canonical(record).decode()),
                )
                for activity in bundle["activities"]:
                    conn.execute(
                        "INSERT OR IGNORE INTO paper_exec_broker_execution_activities VALUES (?,?,?)",
                        (record["namespace"], activity["id"], canonical({
                            "binding": bundle["binding"], "activity": activity,
                        }).decode()),
                    )
                ledger._append_audit(conn, EVENT, key, {"record": record})
            ledger._assert_ready(conn, allow_broker_break=True)
            ledger._commit(conn)
            return _receipt(previous if replay else record, replay=replay)
        except Exception:
            ledger._rollback(conn)
            raise
        finally:
            conn.close()


def projection_breaks(conn: sqlite3.Connection) -> list[str]:
    try:
        present = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('paper_exec_broker_execution_evidence','paper_exec_broker_execution_activities')"
        )}
        if len(present) != 2:
            if conn.execute("SELECT 1 FROM paper_exec_audit WHERE event=? LIMIT 1", (EVENT,)).fetchone():
                return ["broker_execution_evidence_projection"]
            for table in present:
                if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                    return ["broker_execution_evidence_projection"]
            return []
        expected, activities = {}, {}
        for row in conn.execute(
            "SELECT client_order_id,payload FROM paper_exec_audit WHERE event=? ORDER BY seq", (EVENT,),
        ):
            payload = json.loads(row[1])
            if not isinstance(payload, dict) or set(payload) != {"record"}:
                raise ValueError()
            saved = payload["record"]
            record = _record(saved["control_db"], saved["bundle"])
            if record != saved or record["bundle"]["client_order_id"] != row[0]:
                raise ValueError()
            if row[0] in expected:
                _compatible(expected[row[0]], record)
            expected[row[0]] = record
            for activity in record["bundle"]["activities"]:
                key = (record["namespace"], activity["id"])
                body = {"binding": record["bundle"]["binding"], "activity": activity}
                if key in activities and activities[key] != body:
                    raise ValueError()
                activities[key] = body
        actual = {row[0]: json.loads(row[1]) for row in conn.execute(
            "SELECT client_order_id,body FROM paper_exec_broker_execution_evidence"
        )}
        actual_activities = {(row[0], row[1]): json.loads(row[2]) for row in conn.execute(
            "SELECT namespace,activity_id,body FROM paper_exec_broker_execution_activities"
        )}
        if actual != expected or actual_activities != activities:
            raise ValueError()
    except (ValueError, KeyError, TypeError, RecursionError, ArithmeticError):
        return ["broker_execution_evidence_projection"]
    return []


def public(conn: sqlite3.Connection) -> list[dict]:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='paper_exec_broker_execution_evidence'").fetchone() is None:
        return []
    return [_receipt(json.loads(row[0]), replay=False) for row in conn.execute(
        "SELECT body FROM paper_exec_broker_execution_evidence ORDER BY client_order_id"
    )]
