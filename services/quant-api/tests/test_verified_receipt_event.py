"""Adversarial tests for verified receipt event envelope parsing."""
import pytest

from osiris_api.verified_receipt_event import (
    VERIFIED_EVENT_PROVENANCE,
    parse_event_envelope,
)


def event():
    return {
        "schema": "osiris.p2p.verified-receipt-event.v1",
        "event_id": "event-1",
        "sender_peer_id": "sender",
        "recipient_peer_id": "recipient",
        "message_id": "message",
        "envelope_sha256": "a" * 64,
        "accepted_at": 1000,
        "provenance": VERIFIED_EVENT_PROVENANCE,
    }


def validate(payload, **overrides):
    kwargs = {"expected_sender_peer_id": "sender", "now": 1000}
    kwargs.update(overrides)
    return parse_event_envelope(payload, **kwargs)


def test_accepts_exact_event_shape():
    assert validate(event()) == ("event-1", "message", "recipient", "a" * 64)


@pytest.mark.parametrize("field,value", [
    ("schema", "untrusted"), ("sender_peer_id", "other"),
    ("recipient_peer_id", ""), ("event_id", "bad\nline"),
    ("message_id", "x" * 257), ("envelope_sha256", "A" * 64),
    ("envelope_sha256", "a" * 63), ("accepted_at", True),
    ("accepted_at", 879), ("accepted_at", 1031),
    ("provenance", "stdout"), ("provenance", True),
])
def test_rejects_malformed_or_unmatched_events(field, value):
    payload = event()
    payload[field] = value
    with pytest.raises(ValueError):
        validate(payload)


def test_rejects_verified_flag_stdout_and_missing_provenance():
    payload = event()
    payload["verified"] = True
    with pytest.raises(ValueError, match="invalid_receipt_event_fields"):
        validate(payload)
    del payload["verified"]
    del payload["provenance"]
    with pytest.raises(ValueError, match="invalid_receipt_event_fields"):
        validate(payload)
    with pytest.raises(ValueError):
        validate("observation_delivery_acknowledged recipient=peer verified=True")


def test_rejects_wrong_local_sender():
    with pytest.raises(ValueError, match="sender_mismatch"):
        validate(event(), expected_sender_peer_id="another")


def test_accepts_window_boundaries():
    for accepted_at in (880, 1030):
        payload = event()
        payload["accepted_at"] = accepted_at
        assert validate(payload)[0] == "event-1"


@pytest.mark.parametrize("bad_sender", ["", "bad sender", "x" * 257, "bad\npeer"])
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
