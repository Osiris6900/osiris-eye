from fastapi.testclient import TestClient

from osiris_api.main import app

client = TestClient(app)


def sample_events():
    return [
        {"event_id": "evt-1", "timestamp": "2026-11-02T20:00:00Z",
         "actor": "operator", "action": "risk_review", "details": {"decision": "hold"}},
        {"event_id": "evt-2", "timestamp": "2026-11-02T20:01:00Z",
         "actor": "system", "action": "data_quality_check", "details": {"status": "pass"}},
    ]


def test_phase15_builds_and_verifies_audit_chain():
    built = client.post("/platform/audit/chain-build", json={"events": sample_events()})
    assert built.status_code == 200
    chain = built.json()
    assert chain["status"] == "valid"
    assert chain["event_count"] == 2
    assert chain["durable_storage"] is False
    verified = client.post("/platform/audit/chain-verify", json={"chain": chain})
    assert verified.status_code == 200
    assert verified.json()["status"] == "valid"
    assert verified.json()["new_risk_allowed"] is False
    assert verified.json()["live_execution_enabled"] is False


def test_phase15_detects_event_tampering_and_reordering():
    chain = client.post("/platform/audit/chain-build", json={"events": sample_events()}).json()
    chain["events"][0]["event"]["details"]["decision"] = "approve"
    result = client.post("/platform/audit/chain-verify", json={"chain": chain})
    assert result.status_code == 200
    assert result.json()["status"] == "tampered_or_invalid"
    assert "hash_mismatch" in {issue["code"] for issue in result.json()["issues"]}


def test_phase15_rejects_duplicate_ids_and_non_json_values():
    duplicate = sample_events()
    duplicate[1]["event_id"] = duplicate[0]["event_id"]
    response = client.post("/platform/audit/chain-build", json={"events": duplicate})
    assert response.status_code == 422
    invalid = sample_events()
    invalid[0]["details"] = ["not", "an", "object"]
    response = client.post("/platform/audit/chain-build", json={"events": invalid})
    assert response.status_code == 422
