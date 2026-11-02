import pytest

from osiris_api.phase16_durable_audit import DurableAuditStore
from osiris_api.phase17_audit_checkpoint import (
    create_audit_checkpoint, verify_audit_checkpoint,
)


def event(event_id):
    return {"event_id": event_id, "timestamp": "2026-11-03T00:00:00Z",
            "actor": "operator", "action": "risk_gate", "details": {"allowed": False}}


def test_checkpoint_matches_and_survives_later_appends(tmp_path):
    store = DurableAuditStore(str(tmp_path / "audit.db"))
    store.append(event("a"))
    store.append(event("b"))
    checkpoint = create_audit_checkpoint(store, "vault-2026-11-03")
    store.append(event("c"))
    result = verify_audit_checkpoint(store, checkpoint)
    assert result["status"] == "matched"
    assert result["current_event_count"] == 3
    assert result["live_execution_enabled"] is False


def test_checkpoint_detects_truncation(tmp_path):
    path = str(tmp_path / "audit.db")
    store = DurableAuditStore(path)
    store.append(event("a"))
    store.append(event("b"))
    checkpoint = create_audit_checkpoint(store, "external-copy")
    with store._connect() as db:
        db.execute("DELETE FROM audit_events WHERE sequence = 1")
    result = verify_audit_checkpoint(store, checkpoint)
    assert result["status"] == "checkpoint_mismatch"
    assert "checkpoint_sequence_missing_possible_rollback" in result["issues"]


def test_checkpoint_rejects_invalid_journal_and_bad_input(tmp_path):
    store = DurableAuditStore(str(tmp_path / "audit.db"))
    with pytest.raises(ValueError, match="empty"):
        create_audit_checkpoint(store, "anchor")
    with pytest.raises(ValueError, match="missing"):
        verify_audit_checkpoint(store, {})
