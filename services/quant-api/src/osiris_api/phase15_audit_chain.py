"""Phase 15 tamper-evident audit-chain primitives.

This module verifies caller-supplied evidence. It is not a durable audit store,
a signature system, or proof that an actor was authenticated.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _canonical_event(event: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            event, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("event must contain finite JSON-compatible values") from exc


def build_audit_chain(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a deterministic SHA-256 hash chain for validated audit events."""
    if not isinstance(events, list):
        raise ValueError("events must be a list")
    previous_hash = "0" * 64
    chained = []
    seen_ids: set[str] = set()
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError(f"event {index} must be an object")
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError(f"event {index} requires a non-empty event_id")
        if event_id in seen_ids:
            raise ValueError(f"duplicate event_id: {event_id}")
        if not isinstance(event.get("timestamp"), str) or not event["timestamp"].strip():
            raise ValueError(f"event {index} requires a timestamp string")
        if not isinstance(event.get("actor"), str) or not event["actor"].strip():
            raise ValueError(f"event {index} requires an actor")
        if not isinstance(event.get("action"), str) or not event["action"].strip():
            raise ValueError(f"event {index} requires an action")
        if "details" in event and not isinstance(event["details"], dict):
            raise ValueError(f"event {index} details must be an object")
        seen_ids.add(event_id)
        envelope = {"sequence": index, "previous_hash": previous_hash, "event": event}
        digest = hashlib.sha256(_canonical_event(envelope)).hexdigest()
        chained.append({**envelope, "hash": digest})
        previous_hash = digest
    return {
        "status": "valid",
        "event_count": len(chained),
        "head_hash": previous_hash,
        "events": chained,
        "durable_storage": False,
        "actor_authentication_verified": False,
    }


def verify_audit_chain(chain: dict[str, Any]) -> dict[str, Any]:
    """Verify sequence, linkage, event schema, and every digest in a supplied chain."""
    if not isinstance(chain, dict) or not isinstance(chain.get("events"), list):
        raise ValueError("chain must contain an events list")
    events = chain["events"]
    previous_hash = "0" * 64
    issues = []
    seen_ids: set[str] = set()
    for index, item in enumerate(events):
        if not isinstance(item, dict):
            issues.append({"index": index, "code": "invalid_entry"})
            continue
        event = item.get("event")
        if not isinstance(event, dict):
            issues.append({"index": index, "code": "invalid_event"})
            continue
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            issues.append({"index": index, "code": "missing_event_id"})
        elif event_id in seen_ids:
            issues.append({"index": index, "code": "duplicate_event_id"})
        else:
            seen_ids.add(event_id)
        if item.get("sequence") != index:
            issues.append({"index": index, "code": "sequence_mismatch"})
        if item.get("previous_hash") != previous_hash:
            issues.append({"index": index, "code": "previous_hash_mismatch"})
        try:
            expected = hashlib.sha256(_canonical_event({
                "sequence": index, "previous_hash": previous_hash, "event": event
            })).hexdigest()
        except ValueError:
            issues.append({"index": index, "code": "invalid_event_payload"})
            continue
        if item.get("hash") != expected:
            issues.append({"index": index, "code": "hash_mismatch"})
        previous_hash = item.get("hash") if isinstance(item.get("hash"), str) else expected
    if chain.get("head_hash") != previous_hash:
        issues.append({"code": "head_hash_mismatch"})
    if chain.get("event_count") != len(events):
        issues.append({"code": "event_count_mismatch"})
    return {
        "status": "valid" if not issues else "tampered_or_invalid",
        "event_count": len(events),
        "verified_head_hash": previous_hash,
        "issue_count": len(issues),
        "issues": issues,
        "new_risk_allowed": False,
        "live_execution_enabled": False,
    }
