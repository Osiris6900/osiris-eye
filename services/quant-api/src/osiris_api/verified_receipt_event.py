"""Fail-closed verification of authenticated Rust receipt event envelopes.

This module is a *transport-independent* verifier. It does not create an
authenticated transport or accept untrusted stdout as proof of Rust verification.
Only a trusted adapter with independently authenticated Rust process identity
may invoke validate_trusted_event.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

_SCHEMA = "osiris.p2p.verified-receipt-event.v1"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_REQUIRED = frozenset({
    "schema", "event_id", "sender_peer_id", "recipient_peer_id",
    "message_id", "envelope_sha256", "accepted_at",
})


def validate_trusted_event(
    event: Mapping[str, object],
    *,
    authenticated_rust_peer: bool,
    expected_sender_peer_id: str,
    now: int,
    max_age_seconds: int = 120,
    max_future_skew_seconds: int = 30,
) -> tuple[str, str, str, str]:
    """Return (event_id, message_id, recipient, digest) or reject.

    authenticated_rust_peer must be supplied by a separately authenticated
    transport, never copied from event data or a caller-provided HTTP body.
    This function does not validate a receipt signature.
    """
    if authenticated_rust_peer is not True:
        raise ValueError("unauthenticated_receipt_event")
    if not isinstance(event, Mapping) or set(event) != _REQUIRED:
        raise ValueError("invalid_receipt_event_fields")
    if event["schema"] != _SCHEMA:
        raise ValueError("invalid_receipt_event_schema")
    if not isinstance(expected_sender_peer_id, str) or not expected_sender_peer_id:
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
    if not 0 <= max_age_seconds <= 3600 or not 0 <= max_future_skew_seconds <= 300:
        raise ValueError("invalid_receipt_event_window")
    if not now - max_age_seconds <= accepted_at <= now + max_future_skew_seconds:
        raise ValueError("stale_receipt_event")
    return (
        event["event_id"], event["message_id"],
        event["recipient_peer_id"], digest,
    )
