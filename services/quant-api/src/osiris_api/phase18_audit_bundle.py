"""Portable, offline-verifiable audit evidence bundles.

The bundle hash covers the journal. An Ed25519 signature over that hash is
attached when ``OSIRIS_AUDIT_SIGNING_KEY_FILE`` is set. With no key, the bundle
is marked unsigned and is not reported as signed. ``status`` remains the hash
result (``valid`` or ``invalid``). ``evidence_status`` is the fail-closed
result: ``ok``, ``tampered``, or ``unverifiable``.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .phase15_audit_chain import _canonical_event
from .phase16_durable_audit import DurableAuditStore


def export_audit_bundle(store: DurableAuditStore, bundle_id: str,
                        checkpoint: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(bundle_id, str) or not bundle_id.strip():
        raise ValueError("bundle_id must be non-empty")
    verified = store.verify()
    if verified["status"] != "valid":
        raise ValueError("cannot export an invalid audit journal")
    if verified["event_count"] == 0:
        raise ValueError("cannot export an invalid empty audit journal")
    with store._connect() as db:
        rows = db.execute(
            "SELECT sequence,event_id,previous_hash,event_json,event_hash "
            "FROM audit_events ORDER BY sequence"
        ).fetchall()
    events = []
    for row in rows:
        events.append({
            "sequence": row["sequence"],
            "event_id": row["event_id"],
            "previous_hash": row["previous_hash"],
            "event": json.loads(row["event_json"]),
            "event_hash": row["event_hash"],
        })
    bundle = {
        "schema_version": 1,
        "bundle_id": bundle_id,
        "algorithm": "sha256",
        "event_count": len(events),
        "head_hash": verified["head_hash"],
        "events": events,
        "checkpoint": checkpoint,
        "live_execution_enabled": False,
    }
    bundle["bundle_hash"] = hashlib.sha256(_canonical_event(bundle)).hexdigest()
    from .audit_evidence import attach_bundle_signature
    return attach_bundle_signature(bundle)


def verify_audit_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(bundle, dict):
        raise ValueError("bundle must be an object")
    required = {"schema_version", "bundle_id", "algorithm", "event_count",
                "head_hash", "events", "checkpoint", "bundle_hash"}
    if not required.issubset(bundle):
        raise ValueError("bundle is missing required fields")
    if bundle["schema_version"] != 1 or bundle["algorithm"] != "sha256":
        raise ValueError("unsupported bundle schema or algorithm")
    if not isinstance(bundle["bundle_id"], str) or not bundle["bundle_id"].strip():
        raise ValueError("bundle_id must be non-empty")
    events = bundle["events"]
    if not isinstance(events, list):
        raise ValueError("bundle events must be a list")
    from .audit_evidence import bundle_actors_authenticated, signature_state

    unsigned = dict(bundle)
    envelope = unsigned.pop("signature_envelope", None)
    declared_signature = unsigned.pop("signature_status", "unsigned")
    unsigned.pop("signed", None)
    unsigned.pop("key_error", None)
    claimed_bundle_hash = unsigned.pop("bundle_hash")
    actual_bundle_hash = hashlib.sha256(_canonical_event(unsigned)).hexdigest()
    issues: list[str] = []
    if bundle["event_count"] != len(events):
        issues.append("event_count_mismatch")
    if not isinstance(claimed_bundle_hash, str) or claimed_bundle_hash != actual_bundle_hash:
        issues.append("bundle_hash_mismatch")
    state = signature_state(envelope, actual_bundle_hash)
    if state == "invalid" or (declared_signature == "signed" and state == "unsigned"):
        issues.append("signature_invalid")
        state = "invalid"

    previous_hash = "0" * 64
    seen_ids: set[str] = set()
    for expected_sequence, row in enumerate(events):
        if not isinstance(row, dict):
            issues.append("invalid_event_record")
            break
        event = row.get("event")
        if not isinstance(event, dict):
            issues.append("invalid_event_payload")
            break
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip() or event_id in seen_ids:
            issues.append("duplicate_or_invalid_event_id")
        seen_ids.add(event_id if isinstance(event_id, str) else "")
        try:
            expected_hash = hashlib.sha256(_canonical_event({
                "sequence": expected_sequence,
                "previous_hash": previous_hash,
                "event": event,
            })).hexdigest()
        except (TypeError, ValueError, OverflowError):
            issues.append("invalid_event_payload")
            break
        if row.get("sequence") != expected_sequence:
            issues.append("sequence_mismatch")
        if row.get("event_id") != event_id:
            issues.append("event_id_mismatch")
        if row.get("previous_hash") != previous_hash:
            issues.append("previous_hash_mismatch")
        if row.get("event_hash") != expected_hash:
            issues.append("event_hash_mismatch")
        previous_hash = row.get("event_hash", "")

    if bundle["head_hash"] != previous_hash:
        issues.append("head_hash_mismatch")
    checkpoint = bundle.get("checkpoint")
    if checkpoint is not None:
        if not isinstance(checkpoint, dict):
            issues.append("invalid_checkpoint")
        else:
            seq = checkpoint.get("sequence")
            if (not isinstance(seq, int) or isinstance(seq, bool) or seq < 0
                    or checkpoint.get("event_count") != seq + 1
                    or seq >= len(events)):
                issues.append("checkpoint_range_mismatch")
            elif events[seq].get("event_hash") != checkpoint.get("head_hash"):
                issues.append("checkpoint_hash_mismatch")
    hash_issues = [issue for issue in issues if issue != "signature_invalid"]
    actors_ok = bundle_actors_authenticated(bundle)
    if hash_issues or state == "invalid":
        evidence_status = "tampered"
    elif state == "verified" and actors_ok:
        evidence_status = "ok"
    else:
        evidence_status = "unverifiable"
    return {
        "status": "valid" if not issues else "invalid",
        "bundle_id": bundle["bundle_id"],
        "event_count": len(events),
        "head_hash": previous_hash,
        "bundle_hash": actual_bundle_hash,
        "issues": issues,
        "bundle_integrity_verified": not hash_issues,
        "signature_verified": state == "verified",
        "signature_status": state,
        "signed": state == "verified",
        "evidence_status": evidence_status,
        "actor_authentication_verified": evidence_status == "ok",
        "trusted_timestamp_verified": False,
        "live_execution_enabled": False,
        "live_trading_authorized": False,
        "new_risk_allowed": False,
    }
