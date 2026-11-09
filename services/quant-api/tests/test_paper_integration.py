"""Deterministic API contract tests for the paper-only integration."""
import hashlib
import hmac
import time

from fastapi.testclient import TestClient

from osiris_api.main import app
from osiris_api.paper_integration import PaperSignalRequest, observation_message


def signed_payload(**overrides):
    payload = dict(peer_id="peer-a", instrument="BTC-USD", observed_at=int(time.time()),
                   price=100.0, signal=0.8, quantity=1.0,
                   current_position_notional=0.0, daily_pnl=0.0)
    payload.update(overrides)
    wire = PaperSignalRequest(**payload, signature="0" * 64)
    payload["signature"] = hmac.new(b"test-secret", observation_message(wire), hashlib.sha256).hexdigest()
    return payload


def test_fail_closed_without_trust(monkeypatch):
    monkeypatch.delenv("OSIRIS_API_TOKEN", raising=False)
    monkeypatch.delenv("OSIRIS_OBSERVATION_HMAC_KEY", raising=False)
    monkeypatch.delenv("OSIRIS_TRUSTED_PEERS", raising=False)
    response = TestClient(app).post("/integration/paper-signal", json=signed_payload())
    assert response.status_code == 200
    assert response.json()["reason"] == "trust_not_configured"
    assert not response.json()["approved"]


def test_signed_paper_flow_and_rejections(monkeypatch):
    monkeypatch.delenv("OSIRIS_API_TOKEN", raising=False)
    monkeypatch.setenv("OSIRIS_OBSERVATION_HMAC_KEY", "test-secret")
    monkeypatch.setenv("OSIRIS_TRUSTED_PEERS", "peer-a")
    client = TestClient(app)
    ok = client.post("/integration/paper-signal", json=signed_payload())
    assert ok.status_code == 200
    assert ok.json()["approved"] is True
    assert ok.json()["mode"] == "paper_only"
    cases = [
        (signed_payload(peer_id="peer-b"), "untrusted_peer"),
        (signed_payload(observed_at=int(time.time()) - 120), "stale_or_future_observation"),
        (signed_payload(signal=0), "research_signal_abstains"),
        (signed_payload(quantity=200), "max_order_notional_exceeded"),
        (signed_payload(daily_pnl=-1000), "daily_loss_limit_reached"),
    ]
    bad_signature = signed_payload()
    bad_signature["signature"] = "0" * 64
    cases.append((bad_signature, "invalid_observation_signature"))
    for payload, reason in cases:
        response = client.post("/integration/paper-signal", json=payload)
        assert response.status_code == 200
        assert response.json()["approved"] is False
        assert response.json()["reason"] == reason


def test_reject_nonfinite_price(monkeypatch):
    monkeypatch.delenv("OSIRIS_API_TOKEN", raising=False)
    response = TestClient(app).post("/integration/paper-signal", json={
        **signed_payload(), "price": "NaN"
    })
    assert response.status_code == 422
