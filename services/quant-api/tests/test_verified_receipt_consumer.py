"""Durable receipt consumer idempotency and correlation tests."""
import json
import os
import socket
import struct

import pytest

from osiris_api.recipient_receipt_ledger import RecipientReceiptLedger
from osiris_api.verified_receipt_consumer import VerifiedReceiptConsumer
from osiris_api.verified_receipt_event import (
    ACK_SCHEMA,
    VERIFIED_EVENT_PROVENANCE,
    VERIFIED_EVENT_SCHEMA,
)
from osiris_api.verified_receipt_unix import LinuxPeerCredentials, receive_verified_receipt


def payload(event_id="evt-1", recipient="peer-2", digest="a" * 64, message_id="message-1"):
    return {
        "schema": VERIFIED_EVENT_SCHEMA,
        "event_id": event_id,
        "sender_peer_id": "peer-1",
        "recipient_peer_id": recipient,
        "message_id": message_id,
        "envelope_sha256": digest,
        "accepted_at": 1000,
        "provenance": VERIFIED_EVENT_PROVENANCE,
    }


def setup(tmp_path):
    ledger = RecipientReceiptLedger(str(tmp_path / "receipts.sqlite3"))
    ledger.register("message-1", "peer-2", "a" * 64, now=990)
    return ledger, VerifiedReceiptConsumer(ledger, sender_peer_id="peer-1")


def deliver(consumer, event, *, now):
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        body = json.dumps(event).encode()
        left.sendall(struct.pack("!I", len(body)) + body)
        disposition = receive_verified_receipt(
            right, consumer, expected_rust_pid=os.getpid(), expected_rust_uid=os.getuid(), now=now,
        )
        length = struct.unpack("!I", left.recv(4))[0]
        ack = json.loads(left.recv(length))
        return disposition, ack
    finally:
        left.close()
        right.close()


def test_commits_and_survives_restart(tmp_path):
    ledger, consumer = setup(tmp_path)
    disposition, ack = deliver(consumer, payload(), now=1000)
    assert disposition == "applied"
    assert ack == {"schema": ACK_SCHEMA, "event_id": "evt-1", "disposition": "applied"}
    assert ledger.status("message-1", "peer-2", "a" * 64) == "acknowledged"
    reopened = VerifiedReceiptConsumer(
        RecipientReceiptLedger(str(tmp_path / "receipts.sqlite3")),
        sender_peer_id="peer-1",
    )
    again, duplicate_ack = deliver(reopened, payload(), now=1001)
    assert again == "duplicate"
    assert duplicate_ack["disposition"] == "duplicate"
    assert ledger.counts()["acknowledged"] == 1
    with ledger._connect() as db:
        acknowledged_at = db.execute(
            "SELECT acknowledged_at FROM recipient_receipts"
        ).fetchone()[0]
    assert acknowledged_at == 1000


@pytest.mark.parametrize("trusted", [False, None, 1, "yes", True])
def test_untrusted_credentials_never_acknowledge(tmp_path, trusted):
    ledger, consumer = setup(tmp_path)
    with pytest.raises(ValueError, match="unauthenticated_receipt_event"):
        consumer.consume(payload(), peer_credentials=trusted, now=1000)
    with pytest.raises(ValueError, match="untrusted_peer_credential"):
        LinuxPeerCredentials(os.getpid(), os.getuid(), object())
    assert ledger.counts()["pending"] == 1


@pytest.mark.parametrize("change,error", [
    ({"recipient_peer_id": "peer-3"}, "not_pending"),
    ({"envelope_sha256": "b" * 64}, "digest_mismatch"),
    ({"message_id": "unknown"}, "not_pending"),
    ({"accepted_at": 700}, "stale_receipt_event"),
    ({"verified": True}, "invalid_receipt_event_fields"),
])
def test_mismatches_fail_closed(tmp_path, change, error):
    ledger, consumer = setup(tmp_path)
    candidate = payload()
    candidate.update(change)
    with pytest.raises(ValueError, match=error):
        deliver(consumer, candidate, now=1000)
    assert ledger.counts()["pending"] == 1


def test_event_id_collision_does_not_change_existing_ack(tmp_path):
    ledger, consumer = setup(tmp_path)
    assert deliver(consumer, payload(), now=1000)[0] == "applied"
    with pytest.raises(ValueError, match="receipt_event_id_conflict"):
        deliver(consumer, payload(digest="b" * 64), now=1000)
    assert ledger.counts()["acknowledged"] == 1


def test_unknown_event_does_not_consume_id(tmp_path):
    _ledger, consumer = setup(tmp_path)
    with pytest.raises(ValueError, match="not_pending"):
        deliver(consumer, payload(event_id="evt-2", recipient="unknown"), now=1000)
    assert deliver(consumer, payload(event_id="evt-2"), now=1000)[0] == "applied"


def test_out_of_order_registration_acks_only_after_the_row_exists(tmp_path):
    path = str(tmp_path / "receipts.sqlite3")
    ledger = RecipientReceiptLedger(path)
    consumer = VerifiedReceiptConsumer(ledger, sender_peer_id="peer-1")
    with pytest.raises(ValueError, match="not_pending"):
        deliver(consumer, payload(), now=1000)
    assert ledger.counts() == {"pending": 0, "acknowledged": 0, "unconfirmed": 0}
    assert ledger.register("message-1", "peer-2", "a" * 64, now=990)
    assert deliver(consumer, payload(), now=1000)[0] == "applied"
    assert ledger.status("message-1", "peer-2", "a" * 64) == "acknowledged"


def test_unconfirmed_can_be_acknowledged_without_becoming_a_retry(tmp_path):
    ledger, consumer = setup(tmp_path)
    assert ledger.mark_unconfirmed("message-1", "peer-2")
    assert ledger.status("message-1", "peer-2", "a" * 64) == "unconfirmed"
    assert deliver(consumer, payload(), now=1000)[0] == "applied"
    assert ledger.counts() == {"pending": 0, "acknowledged": 1, "unconfirmed": 0}
    assert ledger.outstanding() == []
