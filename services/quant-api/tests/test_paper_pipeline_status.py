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
