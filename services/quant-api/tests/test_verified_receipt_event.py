"""Adversarial tests for the trusted Rust receipt event validation boundary."""
import pytest

from osiris_api.verified_receipt_event import validate_trusted_event


def event():
    return {
        "schema": "osiris.p2p.verified-receipt-event.v1",
        "event_id": "event-1",
        "sender_peer_id": "sender",
        "recipient_peer_id": "recipient",
        "message_id": "message",
        "envelope_sha256": "a" * 64,
        "accepted_at": 1000,
    }


def validate(payload, **overrides):
    kwargs = {
        "authenticated_rust_peer": True,
        "expected_sender_peer_id": "sender",
        "now": 1000,
    }
    kwargs.update(overrides)
    return validate_trusted_event(payload, **kwargs)


def test_accepts_exact_authenticated_event():
    assert validate(event()) == ("event-1", "message", "recipient", "a" * 64)


@pytest.mark.parametrize("authentication", [False, None, 1, "true"])
def test_rejects_untrusted_transport(authentication):
    with pytest.raises(ValueError, match="unauthenticated"):
        validate(event(), authenticated_rust_peer=authentication)


@pytest.mark.parametrize("field,value", [
    ("schema", "untrusted"), ("sender_peer_id", "other"),
    ("recipient_peer_id", ""), ("event_id", "bad\nline"),
    ("message_id", "x" * 257), ("envelope_sha256", "A" * 64),
    ("envelope_sha256", "a" * 63), ("accepted_at", True),
    ("accepted_at", 879), ("accepted_at", 1031),
])
def test_rejects_malformed_or_unmatched_events(field, value):
    payload = event()
    payload[field] = value
    with pytest.raises(ValueError):
        validate(payload)


def test_rejects_extra_fields_and_missing_fields():
    payload = event()
    payload["verified"] = True
    with pytest.raises(ValueError):
        validate(payload)
    del payload["verified"]
    del payload["event_id"]
    with pytest.raises(ValueError):
        validate(payload)


def test_rejects_wrong_local_sender():
    with pytest.raises(ValueError, match="sender_mismatch"):
        validate(event(), expected_sender_peer_id="another")


def test_accepts_window_boundaries():
    for accepted_at in (880, 1030):
        payload = event()
        payload["accepted_at"] = accepted_at
        assert validate(payload)[0] == "event-1"


@pytest.mark.parametrize("bad_sender", ["", "bad sender", "x" * 257, "bad\\npeer"])
def test_rejects_invalid_expected_sender(bad_sender):
    with pytest.raises(ValueError, match="invalid_expected_sender"):
        validate(event(), expected_sender_peer_id=bad_sender)


@pytest.mark.parametrize("bad_window", [True, False, 1.5, "120", None])
def test_rejects_noninteger_age_window(bad_window):
    with pytest.raises(ValueError, match="invalid_receipt_event_window"):
        validate(event(), max_age_seconds=bad_window)


@pytest.mark.parametrize("bad_window", [True, False, 1.5, "30", None])
def test_rejects_noninteger_future_window(bad_window):
    with pytest.raises(ValueError, match="invalid_receipt_event_window"):
        validate(event(), max_future_skew_seconds=bad_window)
