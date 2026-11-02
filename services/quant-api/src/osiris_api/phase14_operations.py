"""Phase 14 operational data-quality and recovery gates."""
from __future__ import annotations

import math
from typing import Any

from .phase4_systems import parse_utc


def audit_data_quality(observations: list[dict[str, Any]], as_of: str,
                       max_age_seconds: float = 60.0) -> dict[str, Any]:
    if not isinstance(observations, list):
        raise ValueError("observations must be a list")
    if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float)) or not math.isfinite(max_age_seconds) or max_age_seconds < 0:
        raise ValueError("max_age_seconds must be finite and non-negative")
    cutoff = parse_utc(as_of)
    seen = set()
    issues = []
    latest_event = None
    latest_available = None
    for index, row in enumerate(observations):
        if not isinstance(row, dict):
            issues.append({"index": index, "code": "invalid_record"})
            continue
        key = row.get("record_key")
        if not isinstance(key, str) or not key.strip():
            issues.append({"index": index, "code": "missing_record_key"})
        elif key in seen:
            issues.append({"index": index, "code": "duplicate_record_key", "record_key": key})
        else:
            seen.add(key)
        try:
            event_time = parse_utc(row.get("event_time"))
            available_at = parse_utc(row.get("available_at"))
        except (ValueError, TypeError):
            issues.append({"index": index, "code": "invalid_timestamp"})
            continue
        if available_at < event_time:
            issues.append({"index": index, "code": "availability_precedes_event"})
        if event_time > cutoff or available_at > cutoff:
            issues.append({"index": index, "code": "future_timestamp"})
        latest_event = event_time if latest_event is None else max(latest_event, event_time)
        latest_available = available_at if latest_available is None else max(latest_available, available_at)
    event_age = None if latest_event is None else (cutoff - latest_event).total_seconds()
    availability_age = None if latest_available is None else (cutoff - latest_available).total_seconds()
    if not observations:
        issues.append({"code": "empty_dataset"})
    if event_age is not None and event_age > max_age_seconds:
        issues.append({"code": "stale_event_data"})
    if availability_age is not None and availability_age > max_age_seconds:
        issues.append({"code": "stale_availability_data"})
    codes = sorted({issue["code"] for issue in issues})
    return {
        "status": "pass" if not issues else "blocked",
        "records_checked": len(observations),
        "issue_count": len(issues),
        "issue_codes": codes,
        "issues": issues,
        "latest_event_time": None if latest_event is None else latest_event.isoformat(),
        "latest_available_at": None if latest_available is None else latest_available.isoformat(),
        "new_risk_allowed": not issues,
    }


def evaluate_recovery_gate(service_checks: list[dict[str, Any]], data_quality_pass: bool,
                           risk_reconciled: bool, manual_approval: bool,
                           consecutive_healthy_checks: int,
                           required_healthy_checks: int = 3) -> dict[str, Any]:
    for name, value in (("data_quality_pass", data_quality_pass),
                        ("risk_reconciled", risk_reconciled),
                        ("manual_approval", manual_approval)):
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be boolean")
    if not isinstance(service_checks, list) or not service_checks:
        raise ValueError("service_checks must be a non-empty list")
    if isinstance(consecutive_healthy_checks, bool) or not isinstance(consecutive_healthy_checks, int) or consecutive_healthy_checks < 0:
        raise ValueError("consecutive_healthy_checks must be a non-negative integer")
    if isinstance(required_healthy_checks, bool) or not isinstance(required_healthy_checks, int) or required_healthy_checks < 1:
        raise ValueError("required_healthy_checks must be a positive integer")
    unhealthy = []
    for item in service_checks:
        if not isinstance(item, dict) or not isinstance(item.get("healthy"), bool) or not item.get("service"):
            raise ValueError("each service check requires service and boolean healthy")
        if not item["healthy"]:
            unhealthy.append(item["service"])
    gates = {
        "services_healthy": not unhealthy,
        "data_quality_pass": data_quality_pass,
        "risk_reconciled": risk_reconciled,
        "consecutive_health_window_met": consecutive_healthy_checks >= required_healthy_checks,
        "manual_approval": manual_approval,
    }
    ready = all(gates.values())
    return {
        "status": "recovery_eligible" if ready else "recovery_blocked",
        "gates": gates,
        "unhealthy_services": unhealthy,
        "consecutive_healthy_checks": consecutive_healthy_checks,
        "required_healthy_checks": required_healthy_checks,
        "new_risk_allowed": ready,
        "live_execution_enabled": False,
    }
