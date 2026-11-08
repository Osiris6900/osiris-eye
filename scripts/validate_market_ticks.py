"""Fail-closed, offline market tick validation; no external feed access."""
from decimal import Decimal, InvalidOperation

def validate_ticks(ticks, max_age_ms, now_ms):
    if not isinstance(ticks, list) or not isinstance(now_ms, int) or not isinstance(max_age_ms, int) or max_age_ms < 0:
        raise ValueError("invalid tick batch configuration")
    seen = set()
    accepted = []
    for tick in ticks:
        if not isinstance(tick, dict):
            raise ValueError("invalid tick")
        symbol, timestamp = tick.get("symbol"), tick.get("timestamp_ms")
        if not isinstance(symbol, str) or not symbol or not isinstance(timestamp, int) or isinstance(timestamp, bool):
            raise ValueError("invalid instrument or timestamp")
        if timestamp > now_ms or now_ms - timestamp > max_age_ms:
            raise ValueError("future or stale tick")
        key = (symbol, timestamp)
        if key in seen:
            raise ValueError("duplicate tick")
        seen.add(key)
        try:
            price = Decimal(str(tick["price"]))
        except (KeyError, InvalidOperation, TypeError, ValueError):
            raise ValueError("invalid price") from None
        if not price.is_finite() or price <= 0:
            raise ValueError("invalid price")
        accepted.append((symbol, timestamp, str(price)))
    return accepted
