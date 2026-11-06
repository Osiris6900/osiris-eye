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
