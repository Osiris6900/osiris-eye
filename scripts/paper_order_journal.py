"""Deterministic, offline paper order journal with restart replay.

Never connects to an exchange. A persisted JSON snapshot is replayed after restart.
All mutations are rejected before append when risk controls or ledger validation fail.
"""
from decimal import Decimal, InvalidOperation
from check_paper_ledger import reconcile


def _positive(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number > 0
    except (InvalidOperation, TypeError, ValueError):
        return False


def apply_command(snapshot, command):
    """Return (new_snapshot, result); caller persists snapshot atomically.

    Repeating an identical command ID returns the original outcome. Reusing it
    for a different payload fails closed, including after a process restart.
    """
    if not isinstance(snapshot, dict) or not isinstance(command, dict):
        raise ValueError("invalid journal input")
    orders = snapshot.get("orders")
    fills = snapshot.get("fills")
    history = snapshot.get("commands")
    if not isinstance(orders, list) or not isinstance(fills, list) or not isinstance(history, dict):
        raise ValueError("invalid journal schema")
    cid = command.get("command_id")
    if not isinstance(cid, str) or not cid:
        raise ValueError("command_id required")
    prior = history.get(cid)
    if prior is not None:
        if prior["command"] != command:
            raise ValueError("idempotency key reused with different payload")
        return snapshot, {"status": "duplicate", "command_id": cid}
    if snapshot.get("kill_switch", False) is not False:
        raise ValueError("kill switch active")
    if snapshot.get("market_data_fresh", False) is not True:
        raise ValueError("stale or unverified market data")
    kind = command.get("type")
    candidate = {**snapshot, "orders": [*orders], "fills": [*fills], "commands": dict(history)}
    if kind == "order":
        order = command.get("order")
        if not isinstance(order, dict) or not _positive(order.get("quantity")):
            raise ValueError("invalid order")
        if any(o.get("id") == order.get("id") for o in orders):
            raise ValueError("duplicate order id")
        candidate["orders"].append(dict(order))
    elif kind == "fill":
        fill = command.get("fill")
        if not isinstance(fill, dict) or not _positive(fill.get("quantity")) or not _positive(fill.get("price")):
            raise ValueError("invalid fill")
        if any(f.get("id") == fill.get("id") for f in fills):
            raise ValueError("duplicate fill id")
        candidate["fills"].append(dict(fill))
    else:
        raise ValueError("unsupported command type")
    # Keep journal-level risk controls aligned with ledger-level controls.
    result = reconcile(candidate)
    if not result["passed"]:
        raise ValueError("; ".join(result["errors"]))
    candidate["commands"][cid] = {"command": command}
    return candidate, {"status": "accepted", "command_id": cid, "ledger": result}


def recover(snapshot):
    """Validate a persisted snapshot before resuming paper simulation."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("commands"), dict):
        raise ValueError("invalid persisted journal")
    for cid, item in snapshot["commands"].items():
        if not isinstance(cid, str) or not cid or not isinstance(item, dict) or not isinstance(item.get("command"), dict) or item["command"].get("command_id") != cid:
            raise ValueError("corrupt command journal")
    # Validate the same controls after a process restart.
    # Historical fills remain valid when a kill switch is activated later.\n    # Execution gates apply to new commands, not to accounting replay.\n    result = reconcile(snapshot)
    if not result["passed"]:
        raise ValueError("; ".join(result["errors"]))
    return result
