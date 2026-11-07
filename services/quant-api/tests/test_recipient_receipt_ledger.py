import json
import os
import socket
import struct

import pytest

from osiris_api.recipient_receipt_ledger import RecipientReceiptLedger
from osiris_api.verified_receipt_consumer import VerifiedReceiptConsumer
from osiris_api.verified_receipt_event import VERIFIED_EVENT_PROVENANCE, VERIFIED_EVENT_SCHEMA
from osiris_api.verified_receipt_unix import receive_verified_receipt

DIGEST = "a" * 64


def acknowledge_via_pinned_channel(ledger, message_id, recipient, *, event_id, now=1000):
    """Move one row through the kernel credential boundary, never a verified flag."""
    if not hasattr(socket, "SO_PEERCRED"):
        pytest.skip("Linux peer credentials are required")
    consumer = VerifiedReceiptConsumer(ledger, sender_peer_id="peer-1")
    event = {
        "schema": VERIFIED_EVENT_SCHEMA,
        "event_id": event_id,
        "sender_peer_id": "peer-1",
        "recipient_peer_id": recipient,
        "message_id": message_id,
        "envelope_sha256": DIGEST,
        "accepted_at": now,
        "provenance": VERIFIED_EVENT_PROVENANCE,
    }
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        body = json.dumps(event).encode()
        left.sendall(struct.pack("!I", len(body)) + body)
        return receive_verified_receipt(
            right, consumer, expected_rust_pid=os.getpid(), expected_rust_uid=os.getuid(), now=now,
        )
    finally:
        left.close()
        right.close()


def test_restart_keeps_pending_and_rejects_direct_verification_flags(tmp_path):
    path = str(tmp_path / "receipt.sqlite3")
    ledger = RecipientReceiptLedger(path)
    assert ledger.register("msg1", "peer1", DIGEST, now=1)
    assert not ledger.register("msg1", "peer1", DIGEST, now=2)
    with pytest.raises(ValueError, match="receipt_reference_conflict"):
        ledger.register("msg1", "peer1", "b" * 64)
    reopened = RecipientReceiptLedger(path)
    assert reopened.counts()["pending"] == 1
    for flag in (False, True, 1, "true"):
        with pytest.raises(ValueError, match="untrusted_verification_flag"):
            reopened.acknowledge_verified("msg1", "peer1", DIGEST, verified=flag, now=3)
    assert reopened.status("msg1", "peer1", DIGEST) == "pending"
    assert acknowledge_via_pinned_channel(reopened, "msg1", "peer1", event_id="evt-msg1") == "applied"
    assert acknowledge_via_pinned_channel(reopened, "msg1", "peer1", event_id="evt-msg1") == "duplicate"
    assert RecipientReceiptLedger(path).counts()["acknowledged"] == 1


def test_unconfirmed_is_not_acknowledged(tmp_path):
    ledger = RecipientReceiptLedger(str(tmp_path / "receipt.sqlite3"))
    ledger.register("msg", "peer", DIGEST)
    assert ledger.mark_unconfirmed("msg", "peer")
    assert not ledger.mark_unconfirmed("msg", "peer")
    assert ledger.counts() == {
        "pending": 0, "acknowledged": 0, "unconfirmed": 1
    }


def test_requires_durable_file():
    with pytest.raises(ValueError):
        RecipientReceiptLedger(":memory:")


def test_outstanding_is_bounded_ordered_and_survives_restart(tmp_path):
    path = str(tmp_path / "receipt.sqlite3")
    ledger = RecipientReceiptLedger(path)
    ledger.register("later", "peer2", DIGEST, now=20)
    ledger.register("early", "peer1", DIGEST, now=10)
    ledger.register("done", "peer3", DIGEST, now=5)
    assert acknowledge_via_pinned_channel(ledger, "done", "peer3", event_id="evt-done") == "applied"
    assert ledger.mark_unconfirmed("early", "peer1")
    rows = RecipientReceiptLedger(path).outstanding(limit=1)
    assert len(rows) == 1
    assert rows[0] == {
        "message_id": "early",
        "recipient_peer_id": "peer1",
        "envelope_sha256": DIGEST,
        "state": "unconfirmed",
        "created_at": 10,
    }
    assert [row["message_id"] for row in ledger.outstanding()] == ["early", "later"]
    assert ledger.counts()["acknowledged"] == 1


@pytest.mark.parametrize("limit", [0, -1, 1001, True, 1.5, "10"])
def test_outstanding_rejects_invalid_limits(tmp_path, limit):
    ledger = RecipientReceiptLedger(str(tmp_path / "receipt.sqlite3"))
    with pytest.raises(ValueError, match="invalid_receipt_limit"):
        ledger.outstanding(limit=limit)


def test_status_requires_exact_correlation_and_survives_restart(tmp_path):
    path = str(tmp_path / "receipt.sqlite3")
    ledger = RecipientReceiptLedger(path)
    assert ledger.status("msg", "peer", DIGEST) is None
    assert ledger.register("msg", "peer", DIGEST)
    assert ledger.status("msg", "peer", DIGEST) == "pending"
    assert ledger.status("msg", "peer", "b" * 64) is None
    assert ledger.status("msg", "other", DIGEST) is None
    assert ledger.mark_unconfirmed("msg", "peer")
    assert RecipientReceiptLedger(path).status("msg", "peer", DIGEST) == "unconfirmed"
    assert acknowledge_via_pinned_channel(ledger, "msg", "peer", event_id="evt-status") == "applied"
    assert RecipientReceiptLedger(path).status("msg", "peer", DIGEST) == "acknowledged"


@pytest.mark.parametrize("message_id,recipient,digest", [
    ("", "peer", DIGEST),
    ("msg", "", DIGEST),
    ("msg", "peer", "invalid"),
    ("msg", "peer", "A" * 64),
])
def test_status_rejects_invalid_references(tmp_path, message_id, recipient, digest):
    ledger = RecipientReceiptLedger(str(tmp_path / "receipt.sqlite3"))
    with pytest.raises(ValueError, match="invalid_receipt_reference"):
        ledger.status(message_id, recipient, digest)


def test_concurrent_same_reference_registers_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = str(tmp_path / "receipt.sqlite3")
    ledger = RecipientReceiptLedger(path)

    def register(_):
        return RecipientReceiptLedger(path).register("shared", "peer", DIGEST)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(register, range(16)))
    assert results.count(True) == 1
    assert results.count(False) == 15
    assert ledger.counts()["pending"] == 1


def test_concurrent_conflicting_digest_never_overwrites(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = str(tmp_path / "receipt.sqlite3")

    def register(digest):
        try:
            return RecipientReceiptLedger(path).register("shared", "peer", digest)
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(register, [DIGEST, "b" * 64]))
    assert results.count(True) == 1
    assert results.count("receipt_reference_conflict") == 1
    assert sum(RecipientReceiptLedger(path).counts().values()) == 1


@pytest.mark.parametrize("untrusted_flag", [1, "true", "yes", [], None, True, False])
def test_direct_verification_flag_cannot_acknowledge(tmp_path, untrusted_flag):
    ledger = RecipientReceiptLedger(str(tmp_path / "receipt.sqlite3"))
    ledger.register("msg", "peer", DIGEST)
    with pytest.raises(ValueError, match="untrusted_verification_flag"):
        ledger.acknowledge_verified("msg", "peer", DIGEST, verified=untrusted_flag)
    assert ledger.status("msg", "peer", DIGEST) == "pending"
