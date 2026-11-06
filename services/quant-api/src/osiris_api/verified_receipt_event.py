"""Structural checks for a verified receipt event envelope.

Parsing an envelope does not authenticate the Rust process and does not
acknowledge delivery. Only the Unix adapter, after a kernel peer-credential
check, may pass the resulting fields to the durable consumer.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

VERIFIED_EVENT_SCHEMA = "osiris.p2p.verified-receipt-event.v1"
VERIFIED_EVENT_PROVENANCE = "rust.delivery_receipt.accept.v1"
ACK_SCHEMA = "osiris.p2p.verified-receipt-ack.v1"
APPLIED = "applied"
DUPLICATE = "duplicate"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_REQUIRED = frozenset({
    "schema", "event_id", "sender_peer_id", "recipient_peer_id",
    "message_id", "envelope_sha256", "accepted_at", "provenance",
})


def parse_event_envelope(
    event: Mapping[str, object],
    *,
    expected_sender_peer_id: str,
    now: int,
    max_age_seconds: int = 120,
    max_future_skew_seconds: int = 30,
) -> tuple[str, str, str, str]:
    """Return (event_id, message_id, recipient, digest) or reject the shape.

    A client-supplied ``verified`` flag is an unexpected field and is rejected.
    This function does not validate a receipt signature.
    """
    if not isinstance(event, Mapping) or set(event) != _REQUIRED:
        raise ValueError("invalid_receipt_event_fields")
    if event["schema"] != VERIFIED_EVENT_SCHEMA:
        raise ValueError("invalid_receipt_event_schema")
    if event["provenance"] != VERIFIED_EVENT_PROVENANCE:
        raise ValueError("invalid_receipt_provenance")
    if (not isinstance(expected_sender_peer_id, str) or
            not 1 <= len(expected_sender_peer_id) <= 256 or
            any(ord(char) < 33 or ord(char) > 126 for char in expected_sender_peer_id)):
        raise ValueError("invalid_expected_sender")
    for field in ("event_id", "sender_peer_id", "recipient_peer_id", "message_id"):
        value = event[field]
        if not isinstance(value, str) or not 1 <= len(value) <= 256 or any(
            ord(char) < 33 or ord(char) > 126 for char in value
        ):
            raise ValueError("invalid_receipt_event_identifier")
    if event["sender_peer_id"] != expected_sender_peer_id:
        raise ValueError("receipt_event_sender_mismatch")
    digest = event["envelope_sha256"]
    if not isinstance(digest, str) or _HEX64.fullmatch(digest) is None:
        raise ValueError("invalid_receipt_event_digest")
    accepted_at = event["accepted_at"]
    if type(accepted_at) is not int or type(now) is not int:
        raise ValueError("invalid_receipt_event_time")
    if (type(max_age_seconds) is not int or type(max_future_skew_seconds) is not int
            or not 0 <= max_age_seconds <= 3600
            or not 0 <= max_future_skew_seconds <= 300):
        raise ValueError("invalid_receipt_event_window")
    if not now - max_age_seconds <= accepted_at <= now + max_future_skew_seconds:
        raise ValueError("stale_receipt_event")
    return (
        event["event_id"], event["message_id"],
        event["recipient_peer_id"], digest,
    )
