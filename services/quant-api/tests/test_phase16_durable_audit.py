import sqlite3

import pytest

from osiris_api.phase16_durable_audit import DurableAuditStore


def event(event_id="durable-1"):
    return {
        "event_id": event_id,
        "timestamp": "2026-11-03T02:00:00Z",
        "actor": "system",
        "action": "risk_gate_evaluated",
        "details": {"new_risk_allowed": False},
    }


def test_phase16_persists_and_verifies_append_chain(tmp_path):
    path = str(tmp_path / "audit.sqlite3")
    first = DurableAuditStore(path)
    appended = first.append(event())
    assert appended["sequence"] == 0
    assert first.append(event("durable-2"))["sequence"] == 1
    reopened = DurableAuditStore(path)
    result = reopened.verify()
    assert result["status"] == "valid"
    assert result["event_count"] == 2
    assert result["storage"] == "sqlite"
    assert result["new_risk_allowed"] is False
    assert result["live_execution_enabled"] is False


def test_phase16_rejects_duplicate_event_ids(tmp_path):
    store = DurableAuditStore(str(tmp_path / "audit.sqlite3"))
    store.append(event())
    with pytest.raises(ValueError, match="duplicate"):
        store.append(event())


def test_phase16_detects_database_tampering(tmp_path):
    path = str(tmp_path / "audit.sqlite3")
    store = DurableAuditStore(path)
    store.append(event())
    with sqlite3.connect(path) as db:
        db.execute("UPDATE audit_events SET event_json = ?", ('{"action":"changed","actor":"system","event_id":"durable-1","timestamp":"2026-11-03T02:00:00Z"}',))
    result = store.verify()
    assert result["status"] == "tampered_or_invalid"
    assert "hash_mismatch" in {issue["code"] for issue in result["issues"]}


def test_phase16_rejects_invalid_event_payload(tmp_path):
    store = DurableAuditStore(str(tmp_path / "audit.sqlite3"))
    with pytest.raises(ValueError):
        store.append({"event_id": "x", "timestamp": "t", "actor": "a", "action": "b", "details": []})
