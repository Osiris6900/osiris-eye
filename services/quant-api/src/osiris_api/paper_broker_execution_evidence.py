"""Bounded GET-only collection of Alpaca PAPER execution activities.

FILL activity IDs identify provider records. The provider does not include
final per-execution fees in this contract, so collection authorizes no financial
posting. Rounded cumulative averages are conservatively rejected when exact
individual activity totals cannot establish the reported notional.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from fractions import Fraction
from urllib.parse import urlencode

from .control_plane.database import canonical, digest, identifier
from .paper_broker_execution_store import ACTIVITY_FIELDS, ORIGIN, SCHEMA, _provider_id

INVALID = "broker_execution_evidence_invalid"
INCOMPLETE = "broker_execution_evidence_incomplete"
MOVING = "broker_execution_evidence_moving"


def _progress(snapshot: dict) -> tuple:
    from .control_plane.broker import money

    average = snapshot.get("filled_avg_price")
    return (snapshot["status"], money(snapshot["filled_qty"], positive=False),
            None if average is None else money(average, positive=False))


def _activity(raw: object, order: dict, broker_id: str, retrieved: datetime) -> dict:
    from . import paper_execution as ledger
    from .control_plane.broker import money

    if not isinstance(raw, dict) or not ACTIVITY_FIELDS.issubset(raw):
        raise ValueError(INVALID)
    result = {key: raw[key] for key in ACTIVITY_FIELDS}
    _provider_id(result["id"])
    if (result["order_id"] != broker_id or result["symbol"] != order["symbol"]
            or result["side"] != order["side"].lower()
            or result["activity_type"] != "FILL" or result["type"] not in {"fill", "partial_fill"}):
        raise ValueError(INVALID)
    for field in ("qty", "price", "cum_qty", "leaves_qty"):
        if not isinstance(result[field], str):
            raise ValueError(INVALID)  # noqa: TRY004 - stable broker evidence refusal API
        result[field] = ledger._canonical(money(result[field], positive=field in {"qty", "price"}))
    stamp = ledger._parse_time(result["transaction_time"])
    if stamp is None or stamp > retrieved + timedelta(seconds=60):
        raise ValueError(INVALID)
    result["transaction_time"] = stamp.isoformat()
    price, limit = money(result["price"]), money(order["limit_price"])
    if ((order["side"].upper() == "BUY" and price > limit)
            or (order["side"].upper() == "SELL" and price < limit)):
        raise ValueError(INVALID)
    return result


def collect(service, client_id: str, *, page_size: int = 100, max_pages: int = 10) -> dict:
    """Collect complete observed history; incomplete or moving evidence refuses.

    No caller activity, price, completeness flag or fee is accepted. Collection
    changes neither the transport documents nor the execution ledger.
    """
    from . import paper_broker_reservations as reservations
    from . import paper_execution as ledger
    from .control_plane.broker import _saved_broker_order, money, validate_broker_snapshot

    if (type(page_size) is not int or not 1 <= page_size <= 100
            or type(max_pages) is not int or not 1 <= max_pages <= 10):
        raise ValueError(INVALID)
    key = identifier(client_id)
    if service.ORIGIN != ORIGIN or service.enabled is not True:
        raise ValueError("paper_broker_not_enabled")
    retained = service.db.get("broker-orders", key)
    if retained is None:
        raise ValueError(INVALID)
    state = retained["value"]
    order = _saved_broker_order(state, key)
    if state.get("status") != "acknowledged":
        raise ValueError(INVALID)
    validate_broker_snapshot(order, state.get("response"))
    binding = reservations.broker_binding(service.db.path, order)
    with ledger._lock:
        conn = ledger._connect()
        try:
            conn.execute("BEGIN")
            ledger._assert_ready(conn, allow_broker_break=True)
            commitment = reservations.read(conn, key)
            if (commitment is None or commitment["binding"] != binding
                    or commitment["order"] != order or commitment["response"] != state["response"]):
                raise ValueError(INVALID)
        finally:
            conn.close()
    order_path = "/v2/orders:by_client_order_id?" + urlencode({"client_order_id": key})
    before = service._request("GET", order_path)
    validate_broker_snapshot(order, before, previous=state["response"])
    retrieved = datetime.now(UTC)
    broker_id = before["id"]
    activities, hashes, seen = [], [], set()
    total, notional, bytes_read = Fraction(0), Fraction(0), 0
    token = None
    previous_time = None
    for _page in range(max_pages):
        query = {"order_id": broker_id, "direction": "asc", "page_size": page_size}
        if token is not None:
            query["page_token"] = token
        raw = service._request("GET", "/v2/account/activities/FILL?" + urlencode(query))
        # Hash canonical JSON values; HttpJson has already decoded wire bytes.
        raw_bytes = canonical(raw)
        bytes_read += len(raw_bytes)
        if bytes_read > 1_048_576 or not isinstance(raw, list) or len(raw) > page_size:
            raise ValueError(INVALID)
        hashes.append(digest(raw))
        if not raw:
            break
        for item in raw:
            activity = _activity(item, order, broker_id, retrieved)
            if activity["id"] in seen:
                raise ValueError(INVALID)
            seen.add(activity["id"])
            stamp = ledger._parse_time(activity["transaction_time"])
            total += Fraction(money(activity["qty"]))
            notional += Fraction(money(activity["qty"])) * Fraction(money(activity["price"]))
            if (total != Fraction(money(activity["cum_qty"], positive=False))
                    or Fraction(money(activity["leaves_qty"], positive=False))
                    != Fraction(money(order["quantity"])) - total
                    or (previous_time is not None and stamp < previous_time)):
                raise ValueError(INVALID)
            previous_time = stamp
            activities.append(activity)
        token = activities[-1]["id"]
    else:
        raise ValueError(INCOMPLETE)
    after = service._request("GET", order_path)
    validate_broker_snapshot(order, after, previous=before)
    if _progress(before) != _progress(after):
        raise ValueError(MOVING)
    filled = money(after["filled_qty"], positive=False)
    if total != Fraction(filled) or (filled and notional != Fraction(filled) * Fraction(
        money(after["filled_avg_price"])
    )):
        raise ValueError(INCOMPLETE)
    result = {
        "schema": SCHEMA, "origin": ORIGIN, "binding": binding,
        "client_order_id": key, "broker_order_id": broker_id,
        "control_version": retained["version"], "retrieved_at": retrieved.isoformat(),
        "order": order, "before_snapshot": before, "after_snapshot": after,
        "activities": activities, "raw_page_hashes": hashes, "status": "complete",
        "fee_status": "unknown", "accounting_authorized": False, "live_execution": False,
    }
    canonical(result)
    return result
