"""Read-only paper pipeline status tests."""
from fastapi.testclient import TestClient
from osiris_api.main import app
from osiris_api.paper_pipeline_status import record_paper_workflow


def test_status_endpoint_never_claims_broker_fill():
    client = TestClient(app)
    result = client.get("/paper/pipeline/status")
    assert result.status_code == 200
    assert result.json()["order"] == "none"
    record_paper_workflow({"symbol": "BTC", "risk": {"approved_for_paper": True}})
    result = client.get("/paper/pipeline/status")
    assert result.status_code == 200
    assert result.json()["instrument"] == "BTC"
    assert result.json()["risk"] == "pass"
    assert result.json()["order"] == "none"
    assert result.json()["feed"] == "missing"


def test_intent_is_read_only_and_accounting_unavailable():
    from osiris_api.paper_pipeline_status import get_paper_pipeline_status
    record_paper_workflow({"symbol": "ETH", "risk": {"approved_for_paper": False}, "paper_order_intent": {"side": "BUY", "quantity": 2, "status": "BLOCKED_BY_RISK"}})
    status = get_paper_pipeline_status()
    assert status["intent"]["status"] == "BLOCKED_BY_RISK"
    assert status["positions"] is None
    assert status["cash"] is None
    assert status["pnl"] is None
    status["intent"]["status"] = "FAKE"
    assert get_paper_pipeline_status()["intent"]["status"] == "BLOCKED_BY_RISK"


def test_verified_observation_status_has_provenance_but_no_fill():
    from osiris_api.paper_pipeline_status import get_paper_pipeline_status
    record_paper_workflow({
        "symbol": "BTC", "risk": {"approved_for_paper": True},
        "paper_intent_id": "abc123",
        "paper_order_intent": {"side": "BUY", "status": "PAPER_INTENT_APPROVED"},
        "observation_provenance": {"provider": "approved-source", "event_time": "2026-11-08T00:00:00+00:00"},
    })
    result = get_paper_pipeline_status()
    assert result["feed"] == "valid"
    assert result["source"] == "verified-stored-observation"
    assert result["paper_intent_id"] == "abc123"
    assert result["order"] == "none"
    assert result["pnl"] is None
