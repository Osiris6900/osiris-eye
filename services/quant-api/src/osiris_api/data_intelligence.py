"""Point-in-time observation quality checks. No vendor connectivity is implied."""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Sequence


def _timestamp(value: str, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a timezone-aware ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a timezone-aware ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def audit_observations(
    records: Sequence[dict[str, Any]],
    *,
    as_of: str,
    max_age_seconds: float = 300.0,
) -> dict[str, Any]:
    """Audit a supplied point-in-time batch; callers remain responsible for provenance."""
    if not records:
        raise ValueError("records must not be empty")
    if not isfinite(max_age_seconds) or max_age_seconds < 0:
        raise ValueError("max_age_seconds must be finite and non-negative")
    now = _timestamp(as_of, "as_of")
    seen: set[tuple[str, str, str]] = set()
    issues: list[dict[str, Any]] = []
    stale = 0
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            issues.append({"index": index, "code": "invalid_record"})
            continue
        required = ("dataset", "record_key", "source", "event_time", "available_at", "payload")
        missing = [key for key in required if key not in record or record[key] in (None, "")]
        if missing:
            issues.append({"index": index, "code": "missing_fields", "fields": missing})
            continue
        if not isinstance(record["payload"], dict):
            issues.append({"index": index, "code": "invalid_payload"})
        identity = (str(record["dataset"]), str(record["source"]), str(record["record_key"]))
        if identity in seen:
            issues.append({"index": index, "code": "duplicate_identity"})
        seen.add(identity)
        try:
            event_time = _timestamp(record["event_time"], "event_time")
            available_at = _timestamp(record["available_at"], "available_at")
            if available_at < event_time:
                issues.append({"index": index, "code": "availability_precedes_event"})
            age = (now - available_at).total_seconds()
            if age < 0:
                issues.append({"index": index, "code": "future_available_at"})
            elif age > max_age_seconds:
                stale += 1
                issues.append({"index": index, "code": "stale_observation", "age_seconds": age})
        except ValueError as exc:
            issues.append({"index": index, "code": "invalid_timestamp", "detail": str(exc)})
    blocking = any(item["code"] in {
        "invalid_record", "missing_fields", "invalid_payload", "invalid_timestamp",
        "availability_precedes_event", "future_available_at", "duplicate_identity",
    } for item in issues)
    return {
        "status": "blocked" if blocking else ("degraded" if stale else "healthy"),
        "records_checked": len(records),
        "stale_count": stale,
        "issue_count": len(issues),
        "issues": issues,
        "new_risk_allowed": not blocking and stale == 0,
        "as_of": now.isoformat(),
    }
