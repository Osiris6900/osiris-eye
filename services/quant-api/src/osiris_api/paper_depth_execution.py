"""Conservative, source-bound displayed-depth fills in the canonical paper book.

This is an explicitly invoked local simulation. No venue/broker order, passive
queue fill, hidden liquidity, borrowing or independent execution certification.
"""
from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

from . import paper_execution as ledger
from .control_plane.broker import money
from .control_plane.database import canonical, digest
from .control_plane.depth import DepthService, _exact

MODEL = "conservative-displayed-depth-v1"


def _policy(participation_bps, min_latency_ms, max_age_ms):
    if (type(participation_bps) is not int or not 1 <= participation_bps <= 10_000
            or type(min_latency_ms) is not int or not 0 <= min_latency_ms <= 5000
            or type(max_age_ms) is not int or not 1 <= max_age_ms <= 60_000
            or min_latency_ms > max_age_ms):
        raise ValueError("invalid_paper_depth_policy")
    return {"model": MODEL, "participation_bps": participation_bps,
            "min_latency_ms": min_latency_ms, "max_age_ms": max_age_ms}


def _book(book):
    if (not isinstance(book, dict) or book.get("schema_version") != 2
            or book.get("venue") != "coinbase-exchange"
            or not isinstance(book.get("instrument"), str)
            or not re.fullmatch(r"[A-Z0-9]{2,15}-USD", book["instrument"])
            or type(book.get("sequence")) is not int or not 0 <= book["sequence"] < 2**63
            or type(book.get("captured_ms")) is not int or not 0 <= book["captured_ms"] <= 253402300799000
            or book.get("age_basis") != "local-response-receipt"
            or book.get("source_url") != "https://api.exchange.coinbase.com/products/" + book["instrument"] + "/book?level=2"
            or book.get("snapshot_id") != "book:" + digest({"instrument": book["instrument"], "sequence": book["sequence"]})
            or not isinstance(book.get("raw_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", book["raw_sha256"])
            or not isinstance(book.get("entitlement_reference"), str) or not book["entitlement_reference"].strip()):
        raise ValueError("invalid_paper_depth_snapshot")
    levels = {}
    for side, field in (("BUY", "asks"), ("SELL", "bids")):
        rows = book.get(field)
        if (not isinstance(rows, list) or not 1 <= len(rows) <= 1000
                or type(book.get(field + "_truncated")) is not bool):
            raise ValueError("invalid_paper_depth_levels")
        parsed = []
        for row in rows:
            if not isinstance(row, list) or len(row) != 2:
                raise ValueError("invalid_paper_depth_level")
            parsed.append((Fraction(money(row[0])), Fraction(money(row[1]))))
        prices = [price for price, _ in parsed]
        if len(set(prices)) != len(prices) or prices != sorted(prices, reverse=side == "SELL"):
            raise ValueError("unordered_paper_depth")
        levels[side] = parsed
    if levels["SELL"][0][0] >= levels["BUY"][0][0]:
        raise ValueError("crossed_paper_depth")
    canonical(book)
    return levels


def _select(order, state):
    current = state["books"][order["symbol"]]
    for price, _ in current["levels"][order["side"]]:
        if ((order["side"] == "BUY" and price > order["limit"])
                or (order["side"] == "SELL" and price < order["limit"])):
            break
        available = state["liquidity"].get((order["symbol"], order["side"], price), Fraction(0))
        if available > 0:
            return price, min(order["leaves"], available)
    return None


def replay_depth_state(conn):
    state = {"books": {}, "liquidity": {}, "plans": {}, "orders": {}}
    fills, committed = {}, set()
    for row in conn.execute("SELECT client_order_id,event,payload FROM paper_exec_audit ORDER BY seq"):
        event, key, payload = row["event"], row["client_order_id"], json.loads(row["payload"])
        if event == "order_decided":
            state["orders"][key] = {"symbol": payload["symbol"], "side": payload["side"],
                                     "limit": Fraction(ledger._number(payload["price"], positive=True)),
                                     "leaves": Fraction(ledger._number(payload["quantity"], positive=True)),
                                     "state": payload["state"]}
        elif event == "fill_recorded":
            order = state["orders"][key]
            order["leaves"] -= Fraction(ledger._number(payload["quantity"], positive=True))
            order["state"] = payload["state"]
            if "depth_origin" in payload:
                fills[payload["client_fill_id"]] = (key, payload)
        elif event == "order_cancelled":
            state["orders"][key].update(state="cancelled", leaves=Fraction(0))
        elif event == "depth_snapshot_admitted":
            book, policy = payload["book"], payload["policy"]
            if policy != _policy(policy["participation_bps"], policy["min_latency_ms"], policy["max_age_ms"]):
                raise ValueError("depth_policy_mismatch")
            levels, symbol = _book(book), book["instrument"]
            previous = state["books"].get(symbol)
            if previous and (policy != previous["policy"] or book["sequence"] <= previous["book"]["sequence"]
                             or book["captured_ms"] < previous["book"]["captured_ms"]):
                raise ValueError("depth_snapshot_or_policy_rollback")
            ratio = Fraction(policy["participation_bps"], 10_000)
            for side in ("BUY", "SELL"):
                old = dict(previous["levels"][side]) if previous else {}
                new = dict(levels[side])
                for price in old.keys() | new.keys():
                    resource = (symbol, side, price)
                    prior_capacity = old.get(price, Fraction(0)) * ratio
                    capacity = new.get(price, Fraction(0)) * ratio
                    # Unchanged displayed volume never refills virtual liquidity.
                    # Only observed net increases restore consumed capacity.
                    state["liquidity"][resource] = max(Fraction(0), min(capacity,
                        state["liquidity"].get(resource, Fraction(0)) + capacity - prior_capacity))
            state["books"][symbol] = {"book": book, "levels": levels, "policy": policy, "hash": digest(book)}
        elif event == "depth_plan_completed":
            receipt = payload
            plan = receipt["plan_id"]
            if plan in state["plans"] or receipt["order_id"] != key:
                raise ValueError("duplicate_or_mismatched_depth_plan")
            order = state["orders"][key]
            current = state["books"][order["symbol"]]
            if (receipt["fingerprint"] != digest({"order_id": key, "policy": current["policy"]})
                    or receipt["book_sha256"] != current["hash"]
                    or receipt["book_sequence"] != current["book"]["sequence"]
                    or type(receipt["decision_ms"]) is not int
                    or not current["policy"]["min_latency_ms"] <= receipt["decision_ms"] - current["book"]["captured_ms"] <= current["policy"]["max_age_ms"]):
                raise ValueError("depth_plan_binding_or_time_mismatch")
            if receipt["outcome"] == "filled":
                fill_id = receipt["fill_id"]
                fill_key, fill = fills[fill_id]
                qty, price = Fraction(ledger._number(receipt["quantity"], positive=True)), Fraction(ledger._number(receipt["price"], positive=True))
                before = {**order, "leaves": order["leaves"] + qty}
                if (fill_id in committed or fill_key != key
                        or fill["quantity"] != receipt["quantity"] or fill["price"] != receipt["price"]
                        or fill["depth_origin"] != {"plan_id": plan, "book_sha256": current["hash"]}
                        or _select(before, state) != (price, qty)):
                    raise ValueError("depth_fill_liquidity_mismatch")
                state["liquidity"][(order["symbol"], order["side"], price)] -= qty
                committed.add(fill_id)
            elif (receipt["outcome"] != "no_executable_liquidity" or order["leaves"] <= 0
                    or _select(order, state) is not None or receipt["fill_id"] is not None
                    or receipt["quantity"] != "0" or receipt["price"] is not None):
                raise ValueError("invalid_depth_no_fill")
            state["plans"][plan] = receipt
    if set(fills) != committed:
        raise ValueError("unbound_depth_fill")
    return state


def projection_breaks(conn):
    # Ordinary/legacy ledgers have no depth model to replay.
    if conn.execute("SELECT 1 FROM paper_exec_audit WHERE event IN ('depth_snapshot_admitted','depth_plan_completed') OR (event='fill_recorded' AND instr(payload,'\"depth_origin\"')>0) LIMIT 1").fetchone() is None:
        return []
    try:
        replay_depth_state(conn)
    except (ValueError, TypeError, KeyError, ArithmeticError):
        return ["depth_projection"]
    return []


def prepare(conn, order, context):
    if ledger._journal_schema(conn) != ledger.JOURNAL_SCHEMA:
        raise ValueError("depth_requires_bound_order_journal")
    state = replay_depth_state(conn)
    plan_id, policy = context["plan_id"], context["policy"]
    fingerprint = digest({"order_id": order["client_order_id"], "policy": policy})
    previous = state["plans"].get(plan_id)
    if previous:
        if previous["fingerprint"] != fingerprint:
            raise ValueError("depth_plan_id_conflict")
        return {"receipt": previous, "replayed": True}
    account = conn.execute("SELECT * FROM paper_exec_meta WHERE id=1").fetchone()
    if bool(account["kill_switch"]):
        raise ValueError("kill_switch_active")
    if context["risk_service_available"] is not True:
        raise ValueError("risk_service_unavailable")
    if order["state"] not in ledger.OPEN_ORDER_STATES:
        raise ValueError("invalid_order_transition")
    service = context["service"]
    book, _age = service.current_snapshot(order["symbol"], max_age_ms=policy["max_age_ms"])
    levels = _book(book)
    decision_ms = service._clock_ms()
    if not policy["min_latency_ms"] <= decision_ms - book["captured_ms"] <= policy["max_age_ms"]:
        raise ValueError("paper_depth_latency_or_freshness_guard")
    old = state["books"].get(order["symbol"])
    if old and (old["policy"] != policy or old["book"]["sequence"] > book["sequence"]):
        raise ValueError("depth_snapshot_or_policy_rollback")
    if old and old["book"]["sequence"] == book["sequence"]:
        if old["hash"] != digest(book):
            raise ValueError("depth_snapshot_conflict")
    else:
        ledger._append_audit(conn, "depth_snapshot_admitted", None, {"book": book, "policy": policy})
        state = replay_depth_state(conn)
    selected = _select(state["orders"][order["client_order_id"]], state)
    receipt = {"plan_id": plan_id, "fingerprint": fingerprint,
               "order_id": order["client_order_id"], "book_sha256": digest(book),
               "book_sequence": book["sequence"], "decision_ms": decision_ms,
               "outcome": "filled" if selected else "no_executable_liquidity",
               "fill_id": "depth-" + digest({"plan_id": plan_id}) if selected else None,
               "price": _exact(selected[0]) if selected else None,
               "quantity": _exact(selected[1]) if selected else "0"}
    # Refresh the target's mark from the same book before independent fill risk.
    observed = datetime.fromtimestamp(book["captured_ms"] / 1000, UTC).isoformat()
    midpoint = _exact((levels["BUY"][0][0] + levels["SELL"][0][0]) / 2)
    conn.execute("INSERT INTO paper_exec_marks VALUES (?,?,?) ON CONFLICT(symbol) DO UPDATE SET price=excluded.price,observed_at=excluded.observed_at",
                 (order["symbol"], midpoint, observed))
    ledger._refresh_unrealized(conn)
    ledger._append_audit(conn, "mark_to_market", None, {"marks": {order["symbol"]: midpoint},
                           "observed_at": observed, "live_execution": False, "broker_order_submitted": False})
    return {"receipt": receipt, "replayed": False, "observed_at": observed,
            "now": datetime.fromtimestamp(decision_ms / 1000, UTC).isoformat()}


def finish(conn, key, plan):
    ledger._append_audit(conn, "depth_plan_completed", key, plan["receipt"])


def execute_depth_fill(service: DepthService, *, client_order_id: str, client_plan_id: str,
                       participation_bps: int = 1000, min_latency_ms: int = 250,
                       max_age_ms: int = 5000, risk_service_available: object = False,
                       simulate_crash: bool = False):
    """One displayed level per call; retries never create another fill.

    The service reads a previously captured immutable book. This method neither
    fetches network data nor initializes an account. Ordinary fill HTTP callers
    cannot provide the internal depth context or claim this source provenance.
    """
    if not isinstance(service, DepthService):
        raise ValueError("configured_depth_service_required")
    ledger._refuse_live(False)
    execution_path = Path(ledger._database()).resolve()
    if execution_path == Path(service.db.path).resolve() or (
        execution_path.exists() and os.path.samefile(execution_path, service.db.path)
    ):
        raise ValueError("separate_depth_and_execution_databases_required")
    plan_id = ledger._text_id(client_plan_id, "invalid_client_plan_id")
    context = {"service": service, "plan_id": plan_id,
               "policy": _policy(participation_bps, min_latency_ms, max_age_ms),
               "risk_service_available": risk_service_available}
    return ledger._apply_paper_fill(
        client_order_id=client_order_id, client_fill_id="depth-" + digest({"plan_id": plan_id}),
        quantity="0", simulate_crash=simulate_crash, depth_context=context,
    )
