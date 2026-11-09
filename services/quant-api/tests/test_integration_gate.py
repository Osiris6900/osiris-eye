"""API contract tests: crypto trust -> PIT research -> risk -> paper-only gate."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app

client = TestClient(app)


def valid_request():
    now = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
    return {
        "observation": {
            "source": "verified-peer", "signature_verified": True,
            "authorized_source": True, "license_authorized": True,
            "event_time": (now - timedelta(seconds=2)).isoformat(),
            "available_at": (now - timedelta(seconds=1)).isoformat(),
            "price": 100.0,
        },
        "intent": {
            "model_id": "research-v1", "approved_for_paper": True,
            "quantity": 2.0, "max_notional": 300.0,
        },
        "as_of": now.isoformat(), "max_age_seconds": 30,
        "kill_switch": False, "risk_approved": True,
    }


def test_verified_research_risk_pipeline_is_paper_only():
    response = client.post("/platform/integration/paper-gate", json=valid_request())
    assert response.status_code == 200
    assert response.json()["paper_intent_allowed"] is True
    assert response.json()["live_execution_enabled"] is False
    assert response.json()["order_submitted"] is False


@pytest.mark.parametrize("field,value,reason", [
    ("signature_verified", False, "untrusted_observation"),
    ("authorized_source", False, "untrusted_observation"),
    ("license_authorized", False, "data_not_licensed"),
])
def test_untrusted_data_fails_closed(field, value, reason):
    payload = valid_request()
    payload["observation"][field] = value
    response = client.post("/platform/integration/paper-gate", json=payload)
    assert response.status_code == 200
    assert reason in response.json()["reasons"]
    assert response.json()["paper_intent_allowed"] is False


def test_stale_data_and_future_data_fail_closed():
    payload = valid_request()
    payload["observation"]["available_at"] = "2026-11-08T11:00:00+00:00"
    assert "stale_observation" in client.post("/platform/integration/paper-gate", json=payload).json()["reasons"]
    payload = valid_request()
    payload["observation"]["available_at"] = "2026-11-08T12:01:00+00:00"
    assert "point_in_time_violation" in client.post("/platform/integration/paper-gate", json=payload).json()["reasons"]


@pytest.mark.parametrize("field,value,reason", [
    ("kill_switch", True, "kill_switch_active"),
    ("risk_approved", False, "risk_not_approved"),
])
def test_risk_gates_fail_closed(field, value, reason):
    payload = valid_request()
    payload[field] = value
    result = client.post("/platform/integration/paper-gate", json=payload).json()
    assert reason in result["reasons"]
    assert result["paper_intent_allowed"] is False


def test_unapproved_model_and_notional_breach_block():
    payload = valid_request()
    payload["intent"]["approved_for_paper"] = False
    payload["intent"]["max_notional"] = 10
    result = client.post("/platform/integration/paper-gate", json=payload).json()
    assert set(["model_not_approved", "notional_limit"]) <= set(result["reasons"])


def test_bad_quantity_rejected():
    payload = valid_request()
    payload["intent"]["quantity"] = -1
    assert client.post("/platform/integration/paper-gate", json=payload).status_code == 422
