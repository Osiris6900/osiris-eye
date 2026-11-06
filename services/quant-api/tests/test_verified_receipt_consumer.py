"""Durable receipt consumer idempotency and correlation tests."""
import pytest

from osiris_api.recipient_receipt_ledger import RecipientReceiptLedger
from osiris_api.verified_receipt_consumer import VerifiedReceiptConsumer


def payload(event_id="evt-1", recipient="peer-2", digest="a" * 64):
    return {
        "schema": "osiris.p2p.verified-receipt-event.v1",
        "event_id": event_id,
        "sender_peer_id": "peer-1",
        "recipient_peer_id": recipient,
        "message_id": "message-1",
        "envelope_sha256": digest,
        "accepted_at": 1000,
    }


def setup(tmp_path):
    ledger = RecipientReceiptLedger(str(tmp_path / "receipts.sqlite3"))
    ledger.register("message-1", "peer-2", "a" * 64, now=990)
    return ledger, VerifiedReceiptConsumer(ledger, sender_peer_id="peer-1")


def test_commits_and_survives_restart(tmp_path):
    ledger, consumer = setup(tmp_path)
    assert consumer.consume(payload(), authenticated_rust_peer=True, now=1000)
    assert ledger.status("message-1", "peer-2", "a" * 64) == "acknowledged"
    reopened = VerifiedReceiptConsumer(
        RecipientReceiptLedger(str(tmp_path / "receipts.sqlite3")),
        sender_peer_id="peer-1",
    )
    assert reopened.consume(payload(), authenticated_rust_peer=True, now=1001) is False


@pytest.mark.parametrize("trusted", [False, None, 1, "yes"])
def test_untrusted_events_never_acknowledge(tmp_path, trusted):
    ledger, consumer = setup(tmp_path)
    with pytest.raises(ValueError, match="unauthenticated"):
        consumer.consume(payload(), authenticated_rust_peer=trusted, now=1000)
    assert ledger.counts()["pending"] == 1


@pytest.mark.parametrize("change", [
    {"recipient_peer_id": "peer-3"},
    {"envelope_sha256": "b" * 64},
    {"message_id": "unknown"},
    {"accepted_at": 700},
])
def test_mismatches_fail_closed(tmp_path, change):
    ledger, consumer = setup(tmp_path)
    candidate = payload()
    candidate.update(change)
    with pytest.raises(ValueError):
        consumer.consume(candidate, authenticated_rust_peer=True, now=1000)
    assert ledger.counts()["pending"] == 1


def test_event_id_collision_does_not_change_existing_ack(tmp_path):
    ledger, consumer = setup(tmp_path)
    assert consumer.consume(payload(), authenticated_rust_peer=True, now=1000)
    with pytest.raises(ValueError, match="receipt_event_id_conflict"):
        consumer.consume(payload(digest="b" * 64), authenticated_rust_peer=True, now=1000)
    assert ledger.counts()["acknowledged"] == 1


def test_unknown_event_does_not_consume_id(tmp_path):
    ledger, consumer = setup(tmp_path)
    with pytest.raises(ValueError, match="not_pending"):
        consumer.consume(payload(event_id="evt-2", recipient="unknown"),
                         authenticated_rust_peer=True, now=1000)
    assert consumer.consume(payload(event_id="evt-2"),
                            authenticated_rust_peer=True, now=1000)
