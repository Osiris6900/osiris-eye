"""HTTP behavior for bounded identity enrollment, using isolated registry state."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import osiris_api.p2p_identity as identity
import osiris_api.p2p_routes as routes


@pytest.fixture
def registry_client(tmp_path, monkeypatch):
    registry = identity.P2PIdentityRegistry(str(tmp_path / "identity.sqlite3"))
    monkeypatch.setattr(routes, "identity_registry", registry)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield registry, client


def enrollment_payload(client):
    node = identity.generate_node_identity()
    challenge = client.post("/p2p/challenge").json()
    return {
        "public_key": node["public_key"],
        "signature": identity.sign_enrollment_challenge(
            node["private_key"], challenge["challenge_id"], challenge["nonce"], node["node_id"]
        ),
        "challenge_id": challenge["challenge_id"],
    }


def test_challenge_exhaustion_returns_retryable_429(registry_client, monkeypatch):
    _, client = registry_client
    monkeypatch.setattr(identity, "MAX_ACTIVE_CHALLENGES", 1)
    assert client.post("/p2p/challenge").status_code == 200
    response = client.post("/p2p/challenge")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "300"
    assert response.json() == {"detail": "challenge_capacity_exceeded"}


def test_enrollment_capacity_returns_429_without_consuming_challenge(registry_client, monkeypatch):
    registry, client = registry_client
    monkeypatch.setattr(identity, "MAX_ENROLLED_NODES", 0)
    payload = enrollment_payload(client)
    response = client.post("/p2p/enroll", json=payload)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "300"
    assert response.json() == {"detail": "node_registry_capacity_exceeded"}
    monkeypatch.setattr(identity, "MAX_ENROLLED_NODES", 1)
    assert client.post("/p2p/enroll", json=payload).status_code == 200
    assert registry.status()["node_count"] == 1


def test_used_enrollment_challenge_is_client_error(registry_client):
    _, client = registry_client
    payload = enrollment_payload(client)
    assert client.post("/p2p/enroll", json=payload).status_code == 200
    response = client.post("/p2p/enroll", json=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": "challenge_missing_or_already_used"}


@pytest.mark.parametrize("endpoint,method", [
    ("/p2p/challenge", "create_challenge"), ("/p2p/enroll", "enroll"),
])
def test_unexpected_registry_value_error_hides_internal_details(registry_client, monkeypatch, endpoint, method):
    registry, client = registry_client
    payload = enrollment_payload(client) if method == "enroll" else None

    def fail(*args):
        raise ValueError("database /private/operator/identity.sqlite3 contains internal details")

    monkeypatch.setattr(registry, method, fail)
    response = client.post(endpoint, json=payload)
    assert response.status_code == 500
    assert response.json() == {"detail": "identity_registry_error"}
    assert "/private/operator" not in response.text


def test_invalid_request_and_node_listing_preserve_research_boundary(registry_client):
    _, client = registry_client
    assert client.post("/p2p/enroll", json={}).status_code == 422
    assert client.get("/p2p/nodes").json() == {"nodes": [], "live_execution": False}
    assert client.get("/p2p/status").json()["live_execution"] is False
