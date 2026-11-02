"""Phase 4 market-data ingestion and point-in-time query foundations."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

def parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("timestamp must be ISO-8601 with timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)

def audit_market_batch(provider: str, dataset: str, license_status: str,
                       records: list[dict[str, Any]]) -> dict[str, Any]:
    if not provider.strip() or not dataset.strip():
        raise ValueError("provider and dataset are required")
    if not isinstance(records, list) or not records or len(records) > 10000:
        raise ValueError("records must contain between 1 and 10000 entries")
    issues = []
    seen = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            issues.append({"index": index, "code": "invalid_record"})
            continue
        missing = [k for k in ("symbol", "record_key", "event_time", "available_at") if not record.get(k)]
        if missing:
            issues.append({"index": index, "code": "missing_fields", "fields": missing})
            continue
        try:
            event_time, available_at = parse_utc(record["event_time"]), parse_utc(record["available_at"])
            if available_at < event_time:
                issues.append({"index": index, "code": "availability_before_event"})
        except ValueError:
            issues.append({"index": index, "code": "invalid_timestamp"})
        key = (str(record["symbol"]).upper(), str(record["record_key"]))
        if key in seen:
            issues.append({"index": index, "code": "duplicate_record"})
        seen.add(key)
        if "bid" in record and "ask" in record:
            try:
                if float(record["bid"]) < 0 or float(record["ask"]) < float(record["bid"]):
                    issues.append({"index": index, "code": "invalid_quote"})
            except (TypeError, ValueError):
                issues.append({"index": index, "code": "invalid_quote"})
    licensed = license_status.lower() in {"authorized", "licensed", "owned"}
    if not licensed:
        issues.append({"code": "license_not_verified"})
    return {"provider": provider, "dataset": dataset, "records_checked": len(records),
            "valid": not issues, "issues": issues, "ingestion_allowed": not issues and licensed,
            "license_status": "verified_by_operator" if licensed else "unverified",
            "note": "License declaration is operator-supplied and is not independently verified."}

def normalize_market_record(record: dict[str, Any]) -> dict[str, Any]:
    symbol = str(record["symbol"]).strip().upper()
    if not symbol or len(symbol) > 64:
        raise ValueError("symbol must contain 1 to 64 characters")
    event_time = parse_utc(record["event_time"]).isoformat()
    available_at = parse_utc(record["available_at"]).isoformat()
    if parse_utc(available_at) < parse_utc(event_time):
        raise ValueError("available_at cannot precede event_time")
    payload = {k: v for k, v in record.items()
               if k not in {"record_key", "symbol", "event_time", "available_at"}}
    return {"record_key": str(record["record_key"]), "event_time": event_time,
            "available_at": available_at, "payload": {"symbol": symbol, **payload}}

def audit_instrument_master(instruments: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(instruments, list) or not instruments or len(instruments) > 50000:
        raise ValueError("instruments must contain between 1 and 50000 entries")
    seen, issues = set(), []
    for index, item in enumerate(instruments):
        symbol = str(item.get("symbol", "")).strip().upper() if isinstance(item, dict) else ""
        if not symbol:
            issues.append({"index": index, "code": "missing_symbol"})
        elif symbol in seen:
            issues.append({"index": index, "code": "duplicate_symbol", "symbol": symbol})
        else:
            seen.add(symbol)
        if isinstance(item, dict) and item.get("asset_class") not in {
            "equity", "option", "future", "fx", "rate", "commodity", "crypto", "index"}:
            issues.append({"index": index, "code": "unknown_asset_class"})
    return {"instruments_checked": len(instruments), "unique_symbols": len(seen),
            "valid": not issues, "issues": issues}

def feed_health_snapshot(last_event_time: str, last_available_at: str,
                         as_of: str, max_lag_seconds: float = 60.0) -> dict[str, Any]:
    if max_lag_seconds < 0:
        raise ValueError("max_lag_seconds must be non-negative")
    now = parse_utc(as_of)
    event_lag = (now - parse_utc(last_event_time)).total_seconds()
    availability_lag = (now - parse_utc(last_available_at)).total_seconds()
    stale = event_lag > max_lag_seconds or availability_lag > max_lag_seconds
    future = event_lag < 0 or availability_lag < 0
    return {"status": "unknown" if future else ("stale" if stale else "fresh"),
            "event_lag_seconds": event_lag, "availability_lag_seconds": availability_lag,
            "new_risk_allowed": not stale and not future, "clock_or_timestamp_anomaly": future}
