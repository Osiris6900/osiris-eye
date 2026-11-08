"""Offline fail-closed paper fill reconciliation; never submits orders.

JSON input: {orders:[{id,symbol,side,quantity}],fills:[{id,order_id,quantity,price}]}.
Detects duplicate IDs, orphan fills, overfills, and invalid economics.
"""
import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path

def positive(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number > 0 else None
    except (InvalidOperation, TypeError):
        return None

def reconcile(data):
    errors, orders, filled, fill_ids = [], {}, {}, set()
    if not isinstance(data, dict) or not isinstance(data.get("orders"), list) or not isinstance(data.get("fills"), list):
        return {"passed": False, "errors": ["orders and fills arrays required"]}
    for order in data["orders"]:
        if not isinstance(order, dict):
            errors.append("invalid order object"); continue
        oid = order.get("id")
        if not isinstance(oid, str) or not oid or oid in orders:
            errors.append("missing or duplicate order id"); continue
        if order.get("side") not in ("buy", "sell") or not isinstance(order.get("symbol"), str) or not order["symbol"]:
            errors.append(f"{oid}: invalid symbol or side")
        qty = positive(order.get("quantity"))
        if qty is None:
            errors.append(f"{oid}: invalid order quantity")
        orders[oid] = qty
        filled[oid] = Decimal("0")
    for fill in data["fills"]:
        if not isinstance(fill, dict):
            errors.append("invalid fill object"); continue
        fid, oid = fill.get("id"), fill.get("order_id")
        if not isinstance(fid, str) or not fid or fid in fill_ids:
            errors.append("missing or duplicate fill id"); continue
        fill_ids.add(fid)
        qty, price = positive(fill.get("quantity")), positive(fill.get("price"))
        if oid not in orders:
            errors.append(f"{fid}: orphan fill"); continue
        if qty is None or price is None:
            errors.append(f"{fid}: invalid fill quantity or price"); continue
        filled[oid] += qty
        if orders[oid] is not None and filled[oid] > orders[oid]:
            errors.append(f"{oid}: overfilled")
    return {"passed": not errors, "errors": errors, "filled_quantity": {k: str(v) for k,v in filled.items()}}

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("ledger", type=Path)
    a = p.parse_args()
    result = reconcile(json.loads(a.ledger.read_text()))
    print(json.dumps(result))
    raise SystemExit(not result["passed"])
