"""The dashboard catalog must stay executable through the shared API."""

import json

import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app
from osiris_api.market_mechanics_routes import CATALOG

client = TestClient(app)


def test_catalog_and_registry_are_consistent_and_discoverable():
    response = client.get("/research/market-mechanics/catalog")
    assert response.status_code == 200
    catalog = response.json()
    assert catalog["count"] == len(catalog["modules"]) == 10
    assert catalog["research_only"] is True
    assert catalog["execution_enabled"] is False
    ids = [module["id"] for module in catalog["modules"]]
    endpoints = [module["endpoint"] for module in catalog["modules"]]
    assert len(set(ids)) == len(set(endpoints)) == 10

    registry = client.get("/platform/modules").json()
    assert registry["count"] == len(registry["modules"]) == 107
    assert len({module["id"] for module in registry["modules"]}) == 107
    by_id = {module["id"]: module for module in registry["modules"]}
    schema = client.get("/openapi.json").json()
    for module in catalog["modules"]:
        registered = by_id[module["id"]]
        assert registered["endpoint"] == module["endpoint"]
        assert registered["evidence_strength"] == module["evidence_strength"]
        assert registered["status"] == "prototype"
        assert module["mechanism"] and module["limitations"]
        assert "requestBody" in schema["paths"][module["endpoint"]]["post"]


@pytest.mark.parametrize("module", CATALOG, ids=lambda module: module["id"])
def test_every_dashboard_example_runs_and_retains_evidence_limits(module):
    response = client.post(module["endpoint"], json=module["sample_input"])
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["research_only"] is True
    assert result["execution_enabled"] is False
    assert result["evidence_strength"] == module["evidence_strength"]
    assert result["limitations"]
    # Every result must remain valid JSON, including computed numeric fields.
    json.dumps(result, allow_nan=False)


def test_new_routes_obey_shared_bearer_auth(monkeypatch):
    monkeypatch.setenv("OSIRIS_API_TOKEN", "test-mechanics-token")
    path = "/research/market-mechanics/catalog"
    assert client.get(path).status_code == 401
    assert client.post(CATALOG[0]["endpoint"], json=CATALOG[0]["sample_input"]).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer test-mechanics-token"}).status_code == 200


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_shared_app_returns_json_safe_validation_errors(number):
    payload = json.dumps(CATALOG[0]["sample_input"]).replace('"spot": 100', f'"spot": {number}')
    response = client.post(CATALOG[0]["endpoint"], content=payload,
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["body", "spot"]
    assert "input" not in detail[0]
    json.dumps(detail, allow_nan=False)
