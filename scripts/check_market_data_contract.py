"""Validate point-in-time market observations before research admission.

Input JSON array fields: symbol, source, event_time, available_time,
received_time, price, and optional entitlement. All times ISO-8601 with zone.
This is a strict offline gate, not a vendor feed adapter.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path

def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return dt.astimezone(timezone.utc)

def validate(rows, max_delay_seconds=300):
    errors = []
    if not isinstance(rows, list):
        return ["observations must be an array"]
    if not isinstance(max_delay_seconds, (int, float)) or isinstance(max_delay_seconds, bool) or max_delay_seconds < 0:
        return ["max_delay_seconds must be nonnegative"]
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"{index}: observation must be an object")
            continue
        if not all(isinstance(row.get(k), str) and row[k].strip() for k in ("symbol", "source")):
            errors.append(f"{index}: symbol and source required")
        if row.get("entitlement") != "authorized":
            errors.append(f"{index}: explicit authorized entitlement required")
        try:
            event = timestamp(row.get("event_time"))
            available = timestamp(row.get("available_time"))
            received = timestamp(row.get("received_time"))
            if not event <= available <= received:
                errors.append(f"{index}: event <= available <= received violated")
            if (received - available).total_seconds() > max_delay_seconds:
                errors.append(f"{index}: stale feed")
        except (ValueError, TypeError, OverflowError) as exc:
            errors.append(f"{index}: invalid timestamp: {exc}")
        try:
            price = Decimal(str(row.get("price")))
            if not price.is_finite() or price <= 0:
                errors.append(f"{index}: price must be finite and positive")
        except (InvalidOperation, TypeError):
            errors.append(f"{index}: invalid price")
    return errors

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("observations", type=Path)
    p.add_argument("--max-delay-seconds", type=int, default=300)
    a = p.parse_args()
    if a.max_delay_seconds < 0:
        p.error("max-delay-seconds must be nonnegative")
    failures = validate(json.loads(a.observations.read_text()), a.max_delay_seconds)
    print(json.dumps({"passed": not failures, "errors": failures}))
    raise SystemExit(bool(failures))
