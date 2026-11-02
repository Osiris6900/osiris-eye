"""Operator-held audit checkpoints for rollback/truncation detection.

A checkpoint must be copied to an independently controlled system. Keeping it
only beside the SQLite journal does not protect against privileged rewrites.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .phase16_durable_audit import DurableAuditStore


def create_audit_checkpoint(store: DurableAuditStore, anchor_id: str,
                            anchored_at: str | None = None) -> dict[str, Any]:
    if not isinstance(anchor_id, str) or not anchor_id.strip():
        raise ValueError("anchor_id must be non-empty")
    verified = store.verify()
    if verified["status"] != "valid":
        raise ValueError("cannot checkpoint an invalid audit journal")
    if verified["event_count"] == 0:
        raise ValueError("cannot checkpoint an empty audit journal")
    return {
        "schema_version": 1,
        "anchor_id": anchor_id,
        "anchored_at": anchored_at or datetime.now(timezone.utc).isoformat(),
        "sequence": verified["event_count"] - 1,
        "head_hash": verified["head_hash"],
        "event_count": verified["event_count"],
        "algorithm": "sha256",
        "storage": "sqlite",
        "external_storage_required": True,
    }


def verify_audit_checkpoint(store: DurableAuditStore,
                            checkpoint: dict[str, Any]) -> dict[str, Any]:
    required = ("anchor_id", "sequence", "head_hash", "event_count", "algorithm")
    if not isinstance(checkpoint, dict) or any(k not in checkpoint for k in required):
        raise ValueError("checkpoint is missing required fields")
    if checkpoint["algorithm"] != "sha256":
        raise ValueError("unsupported checkpoint algorithm")
    if not isinstance(checkpoint["sequence"], int) or checkpoint["sequence"] < 0:
        raise ValueError("checkpoint sequence must be a non-negative integer")
    if checkpoint["event_count"] != checkpoint["sequence"] + 1:
        raise ValueError("checkpoint event_count does not match sequence")
    journal = store.verify()
    with store._connect() as db:
        row = db.execute(
            "SELECT event_hash FROM audit_events WHERE sequence = ?",
            (checkpoint["sequence"],),
        ).fetchone()
    issues = []
    if journal["status"] != "valid":
        issues.append("journal_integrity_failure")
    if row is None:
        issues.append("checkpoint_sequence_missing_possible_rollback")
    elif row["event_hash"] != checkpoint["head_hash"]:
        issues.append("checkpoint_hash_mismatch")
    if journal["event_count"] < checkpoint["event_count"]:
        issues.append("journal_shorter_than_checkpoint")
    return {
        "status": "matched" if not issues else "checkpoint_mismatch",
        "anchor_id": checkpoint["anchor_id"],
        "checkpoint_event_count": checkpoint["event_count"],
        "current_event_count": journal["event_count"],
        "issues": issues,
        "external_anchor_authenticity_verified": False,
        "new_risk_allowed": False,
        "live_execution_enabled": False,
    }
