import pytest

from osiris_api.recipient_receipt_ledger import RecipientReceiptLedger

DIGEST = "a" * 64


def test_restart_pending_and_verified_ack(tmp_path):
    path = str(tmp_path / "receipt.sqlite3")
    ledger = RecipientReceiptLedger(path)
    assert ledger.register("msg1", "peer1", DIGEST, now=1)
    assert not ledger.register("msg1", "peer1", DIGEST, now=2)
    with pytest.raises(ValueError, match="receipt_reference_conflict"):
        ledger.register("msg1", "peer1", "b" * 64)
    reopened = RecipientReceiptLedger(path)
    assert reopened.counts()["pending"] == 1
    with pytest.raises(ValueError, match="verified_receipt_required"):
        reopened.acknowledge_verified("msg1", "peer1", DIGEST, verified=False)
    assert not reopened.acknowledge_verified("msg1", "wrong", DIGEST, verified=True)
    assert not reopened.acknowledge_verified("msg1", "peer1", "b" * 64, verified=True)
    assert reopened.acknowledge_verified("msg1", "peer1", DIGEST, verified=True, now=3)
    assert not reopened.acknowledge_verified("msg1", "peer1", DIGEST, verified=True)
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
    assert ledger.acknowledge_verified("done", "peer3", DIGEST, verified=True)
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
    assert ledger.acknowledge_verified("msg", "peer", DIGEST, verified=True)
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
