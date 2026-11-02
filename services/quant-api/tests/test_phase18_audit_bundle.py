import copy

import pytest

from osiris_api.phase16_durable_audit import DurableAuditStore
from osiris_api.phase17_audit_checkpoint import create_audit_checkpoint
from osiris_api.phase18_audit_bundle import export_audit_bundle, verify_audit_bundle


def event(event_id):
    return {"event_id": event_id, "timestamp": "2026-11-03T00:00:00Z",
            "actor": "operator", "action": "risk_gate", "details": {"allowed": False}}


def test_bundle_exports_and_verifies_offline_with_checkpoint(tmp_path):
    store = DurableAuditStore(str(tmp_path / "audit.db"))
    store.append(event("one"))
    checkpoint = create_audit_checkpoint(store, "external-vault")
    store.append(event("two"))
    bundle = export_audit_bundle(store, "evidence-001", checkpoint)
    result = verify_audit_bundle(bundle)
    assert result["status"] == "valid"
    assert result["event_count"] == 2
    assert result["live_execution_enabled"] is False


@pytest.mark.parametrize("mutation", ["payload", "order", "remove", "checkpoint"])
def test_bundle_detects_tampering(tmp_path, mutation):
    store = DurableAuditStore(str(tmp_path / "audit.db"))
    store.append(event("one"))
    store.append(event("two"))
    checkpoint = create_audit_checkpoint(store, "vault")
    bundle = export_audit_bundle(store, "tamper-test", checkpoint)
    changed = copy.deepcopy(bundle)
    if mutation == "payload":
        changed["events"][0]["event"]["details"]["allowed"] = True
    elif mutation == "order":
        changed["events"].reverse()
    elif mutation == "remove":
        changed["events"].pop()
    else:
        changed["checkpoint"]["head_hash"] = "f" * 64
    assert verify_audit_bundle(changed)["status"] == "invalid"


def test_bundle_rejects_malformed_input(tmp_path):
    with pytest.raises(ValueError, match="missing"):
        verify_audit_bundle({})
    store = DurableAuditStore(str(tmp_path / "audit.db"))
    with pytest.raises(ValueError, match="invalid"):
        export_audit_bundle(store, "empty")
