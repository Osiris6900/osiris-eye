from fastapi.testclient import TestClient

from osiris_api.main import app

client = TestClient(app)

def test_alternative_data_reliability_and_agent_endpoints():
    event = client.post("/research/alternative-data/validate-event", json={
        "source": "licensed-vendor", "event_time": "2026-01-01T10:00:00Z",
        "published_at": "2026-01-01T10:01:00Z", "category": "earnings",
        "sentiment": 0.25,
    })
    assert event.status_code == 200
    assert event.json()["valid"] is True

    health = client.post("/platform/reliability/health", json=[
        {"component": "database", "healthy": True, "detail": "reachable"},
        {"component": "market-feed", "healthy": False, "detail": "stale"},
    ])
    assert health.status_code == 200
    assert health.json()["healthy"] is False

    task = client.post("/platform/agents/validate-task", json={
        "role": "risk_review", "objective": "Review risk limits",
        "requires_human_approval": True,
    })
    assert task.status_code == 200
    assert task.json()["human_approval_required"] is True

    denied = client.post("/platform/agents/validate-task", json={
        "role": "risk_review", "objective": "Execute unattended",
        "requires_human_approval": False,
    })
    assert denied.status_code == 422
