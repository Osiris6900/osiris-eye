"""Policy-gated retry: no reminted envelope after a tracked publication."""
import hashlib
import json
import os
import socket
import struct
from datetime import UTC, datetime

import pytest

from osiris_api.receipt_gated_retry import (
    MAX_ATTEMPTS,
    RETRY_ENV,
    AdmissionDecision,
    PublishNotSent,
    ReceiptRetryScheduler,
    backoff_seconds,
    jittered_backoff_seconds,
)
from osiris_api.recipient_receipt_ledger import RecipientReceiptLedger
from osiris_api.verified_receipt_consumer import VerifiedReceiptConsumer
from osiris_api.verified_receipt_event import VERIFIED_EVENT_PROVENANCE, VERIFIED_EVENT_SCHEMA
from osiris_api.verified_receipt_unix import receive_verified_receipt

CLOCK = datetime(2026, 11, 6, 23, 0, 30, tzinfo=UTC)
NOW = 1_762_470_030
RECIPIENT = "peer-b"
DIGEST = "ab" * 32
MESSAGE = "m" * 22


def observation(payload="probe", when="2026-11-06T23:00:00Z"):
    return {
        "schema": "osiris.observation.v1",
        "instrument": "BTC-USD",
        "observed_at": when,
        "provider": "synthetic-retry",
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
    }


def policy(**overrides):
    trusted = overrides.pop("trusted", {RECIPIENT})
    revoked = overrides.pop("revoked", set())
    return AdmissionDecision(trusted, revoked)


@pytest.fixture
def gate(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_P2P_INGEST_ENABLED", "1")
    monkeypatch.delenv(RETRY_ENV, raising=False)
    ledger = RecipientReceiptLedger(str(tmp_path / "ledger.sqlite3"))
    scheduler = ReceiptRetryScheduler(
        str(tmp_path / "retry.sqlite3"), ledger, enabled=True,
    )
    return scheduler, ledger


def tracked(calls):
    def publish(payload, recipient):
        calls.append((payload, recipient))
        return {"status": "tracked", "message_id": MESSAGE, "envelope_sha256": DIGEST}
    return publish


def key_for(payload="probe"):
    obs = observation(payload)
    return ":".join((obs["provider"], obs["instrument"], obs["payload_sha256"]))


def test_disabled_by_default_does_not_schedule(tmp_path, monkeypatch):
    monkeypatch.delenv(RETRY_ENV, raising=False)
    ledger = RecipientReceiptLedger(str(tmp_path / "ledger.sqlite3"))
    scheduler = ReceiptRetryScheduler(str(tmp_path / "retry.sqlite3"), ledger)
    assert scheduler.enabled is False
    with pytest.raises(ValueError, match="receipt_retry_disabled"):
        scheduler.schedule(observation(), RECIPIENT, now=NOW)
    monkeypatch.setenv(RETRY_ENV, "true")
    assert ReceiptRetryScheduler(str(tmp_path / "other.sqlite3"), ledger).enabled is False


def test_tracked_publication_is_not_written_again(gate):
    scheduler, ledger = gate
    calls = []
    assert scheduler.schedule(observation(), RECIPIENT, now=NOW)
    assert scheduler.schedule(observation(), RECIPIENT, now=NOW) is False
    first = scheduler.advance(
        publish=tracked(calls), allowed_providers={"synthetic-retry"},
        policy=policy(), now=NOW, observed_clock=CLOCK,
    )
    assert first[0]["state"] == "published"
    assert len(calls) == 1
    assert ledger.status(MESSAGE, RECIPIENT, DIGEST) == "pending"
    second = scheduler.advance(
        publish=tracked(calls), allowed_providers={"synthetic-retry"},
        policy=policy(), now=NOW + 1, observed_clock=CLOCK,
    )
    assert second == []
    assert len(calls) == 1
    snap = scheduler.snapshot(key_for(), RECIPIENT)
    assert snap["message_id"] == MESSAGE
    assert snap["envelope_sha256"] == DIGEST
    assert snap["state"] == "published"


def test_revoked_and_unknown_recipients_are_not_sent(gate):
    scheduler, _ledger = gate
    calls = []
    scheduler.schedule(observation("revoked"), RECIPIENT, now=NOW)
    scheduler.schedule(observation("other"), "peer-c", now=NOW)
    revoked = scheduler.advance(
        publish=tracked(calls), allowed_providers={"synthetic-retry"},
        policy=policy(revoked={RECIPIENT}), now=NOW, observed_clock=CLOCK,
    )
    unknown = scheduler.advance(
        publish=tracked(calls), allowed_providers={"synthetic-retry"},
        policy=policy(), now=NOW, observed_clock=CLOCK,
    )
    assert {item["reason"] for item in revoked + unknown} >= {
        "recipient_revoked", "recipient_not_allowed",
    }
    assert calls == []
    assert scheduler.snapshot(key_for("revoked"), RECIPIENT)["state"] == "denied"
    assert scheduler.snapshot(key_for("other"), "peer-c")["state"] == "denied"


def test_uncertain_policy_does_not_send(gate):
    scheduler, _ledger = gate
    calls = []
    scheduler.schedule(observation(), RECIPIENT, now=NOW)
    outcome = scheduler.advance(
        publish=tracked(calls), allowed_providers={"synthetic-retry"},
        policy=AdmissionDecision(None, set()), now=NOW, observed_clock=CLOCK,
    )
    assert outcome[0]["reason"] == "policy_uncertain"
    assert calls == []
    assert scheduler.snapshot(key_for(), RECIPIENT)["state"] == "scheduled"
    assert scheduler.snapshot(key_for(), RECIPIENT)["attempts"] == 0


def test_acknowledged_ledger_suppresses_another_write(gate):
    scheduler, ledger = gate
    calls = []
    scheduler.schedule(observation(), RECIPIENT, now=NOW)
    scheduler.advance(
        publish=tracked(calls), allowed_providers={"synthetic-retry"},
        policy=policy(), now=NOW, observed_clock=CLOCK,
    )
    consumer = VerifiedReceiptConsumer(ledger, sender_peer_id="sender")
    event = {
        "schema": VERIFIED_EVENT_SCHEMA,
        "event_id": "evt-ack",
        "sender_peer_id": "sender",
        "recipient_peer_id": RECIPIENT,
        "message_id": MESSAGE,
        "envelope_sha256": DIGEST,
        "accepted_at": NOW,
        "provenance": VERIFIED_EVENT_PROVENANCE,
    }
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        body = json.dumps(event).encode()
        left.sendall(struct.pack("!I", len(body)) + body)
        assert receive_verified_receipt(
            right, consumer, expected_rust_pid=os.getpid(),
            expected_rust_uid=os.getuid(), now=NOW,
        ) == "applied"
    finally:
        left.close()
        right.close()
    scheduler.advance(
        publish=tracked(calls), allowed_providers={"synthetic-retry"},
        policy=policy(), now=NOW + 5, observed_clock=CLOCK,
    )
    assert len(calls) == 1
    assert scheduler.snapshot(key_for(), RECIPIENT)["state"] == "acknowledged"
    assert scheduler.counts()["acknowledged"] == 1


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="Linux peer credentials are required")
def test_direct_verified_flag_still_cannot_acknowledge(gate):
    _scheduler, ledger = gate
    ledger.register(MESSAGE, RECIPIENT, DIGEST, now=NOW)
    with pytest.raises(ValueError, match="untrusted_verification_flag"):
        ledger.acknowledge_verified(MESSAGE, RECIPIENT, DIGEST, verified=True)
    assert ledger.status(MESSAGE, RECIPIENT, DIGEST) == "pending"


def test_restart_holds_a_claimed_attempt_and_keeps_published_identity(gate, tmp_path):
    scheduler, ledger = gate
    calls = []
    scheduler.schedule(observation(), RECIPIENT, now=NOW)
    scheduler.advance(
        publish=tracked(calls), allowed_providers={"synthetic-retry"},
        policy=policy(), now=NOW, observed_clock=CLOCK,
    )
    reopened = ReceiptRetryScheduler(str(tmp_path / "retry.sqlite3"), ledger, enabled=True)
    reopened.advance(
        publish=tracked(calls), allowed_providers={"synthetic-retry"},
        policy=policy(), now=NOW + 1, observed_clock=CLOCK,
    )
    assert len(calls) == 1
    assert reopened.snapshot(key_for(), RECIPIENT)["message_id"] == MESSAGE

    scheduler.schedule(observation("crash"), RECIPIENT, now=NOW)
    with scheduler._connect() as db:
        db.execute(
            """UPDATE receipt_retry_attempts SET state='publishing'
               WHERE observation_key=?""",
            (key_for("crash"),),
        )
    recovered = ReceiptRetryScheduler(str(tmp_path / "retry.sqlite3"), ledger, enabled=True)
    assert recovered.snapshot(key_for("crash"), RECIPIENT)["state"] == "held"
    assert recovered.snapshot(key_for("crash"), RECIPIENT)["reason"] == "crash_while_publishing"
    recovered.advance(
        publish=tracked(calls), allowed_providers={"synthetic-retry"},
        policy=policy(), now=NOW + 2, observed_clock=CLOCK,
    )
    assert len(calls) == 1


def test_backoff_budget_and_receipt_expiry(gate):
    scheduler, ledger = gate
    calls = []

    def fail(payload, recipient):
        calls.append(recipient)
        raise PublishNotSent("stdin closed")

    scheduler.schedule(observation(), RECIPIENT, now=NOW)

    def delay(failures):
        return jittered_backoff_seconds(
            failures, observation_key=key_for(), recipient=RECIPIENT,
        )

    for index in range(MAX_ATTEMPTS - 1):
        when = NOW + sum(delay(item) for item in range(1, index + 1))
        outcome = scheduler.advance(
            publish=fail, allowed_providers={"synthetic-retry"},
            policy=policy(), now=when, observed_clock=CLOCK,
        )
        assert outcome[0]["state"] == "scheduled"
        early = scheduler.advance(
            publish=fail, allowed_providers={"synthetic-retry"},
            policy=policy(), now=when, observed_clock=CLOCK,
        )
        assert early == []
    last = NOW + sum(delay(item) for item in range(1, MAX_ATTEMPTS))
    exhausted = scheduler.advance(
        publish=fail, allowed_providers={"synthetic-retry"},
        policy=policy(), now=last, observed_clock=CLOCK,
    )
    assert exhausted[0]["reason"] == "retry_budget_exhausted"
    assert len(calls) == MAX_ATTEMPTS
    assert backoff_seconds(1) == 2
    assert backoff_seconds(10) == 60

    scheduler.schedule(observation("wait"), RECIPIENT, now=NOW)
    scheduler.advance(
        publish=tracked([]), allowed_providers={"synthetic-retry"},
        policy=policy(), now=NOW, observed_clock=CLOCK,
    )
    quiet = []
    scheduler.advance(
        publish=tracked(quiet), allowed_providers={"synthetic-retry"},
        policy=policy(), now=NOW + 120, observed_clock=CLOCK,
    )
    assert quiet == []
    assert scheduler.snapshot(key_for("wait"), RECIPIENT)["state"] == "unconfirmed"
    assert ledger.status(MESSAGE, RECIPIENT, DIGEST) == "unconfirmed"


def test_jitter_is_deterministic_bounded_and_spread():
    key = key_for()
    for failures in (1, 2, 3, 4, 10):
        base = backoff_seconds(failures)
        first = jittered_backoff_seconds(
            failures, observation_key=key, recipient=RECIPIENT,
        )
        again = jittered_backoff_seconds(
            failures, observation_key=key, recipient=RECIPIENT,
        )
        assert first == again
        assert base // 2 <= first <= base
    with pytest.raises(ValueError, match="invalid_backoff_attempt"):
        jittered_backoff_seconds(0, observation_key=key, recipient=RECIPIENT)
    with pytest.raises(ValueError, match="invalid_jitter_material"):
        jittered_backoff_seconds(1, observation_key=key, recipient=None)
    spread = {
        jittered_backoff_seconds(3, observation_key=key, recipient=f"peer-{index}")
        for index in range(32)
    }
    assert len(spread) > 1


def test_jitter_spreads_peer_wakeups(gate):
    scheduler, _ledger = gate
    recipients = [f"peer-{index}" for index in range(16)]
    for recipient in recipients:
        scheduler.schedule(observation(), recipient, now=NOW)

    def fail(payload, recipient):
        raise PublishNotSent("stdin closed")

    outcome = scheduler.advance(
        publish=fail, allowed_providers={"synthetic-retry"},
        policy=AdmissionDecision(set(recipients), set()), now=NOW, observed_clock=CLOCK,
    )
    assert all(item["state"] == "scheduled" for item in outcome)
    wakeups = {
        scheduler.snapshot(key_for(), recipient)["next_attempt_at"]
        for recipient in recipients
    }
    assert len(wakeups) > 1
    assert all(NOW + 1 <= when <= NOW + 2 for when in wakeups)


def test_stale_observation_expires_without_a_write(gate):
    scheduler, _ledger = gate
    calls = []
    scheduler.schedule(observation(when="2020-01-01T00:00:00Z"), RECIPIENT, now=NOW)
    outcome = scheduler.advance(
        publish=tracked(calls), allowed_providers={"synthetic-retry"},
        policy=policy(), now=NOW, observed_clock=CLOCK,
    )
    assert outcome[0]["state"] == "expired"
    assert calls == []


def test_unavailable_receipt_channel_does_not_write(gate):
    scheduler, ledger = gate
    calls = []
    scheduler.schedule(observation(), RECIPIENT, now=NOW)
    outcome = scheduler.advance(
        publish=tracked(calls), allowed_providers={"synthetic-retry"},
        policy=policy(), now=NOW, observed_clock=CLOCK, channel_ready=False,
    )
    assert outcome[0]["reason"] == "receipt_channel_unavailable"
    assert outcome[0]["state"] == "scheduled"
    assert calls == []
    assert ledger.counts()["pending"] == 0
    assert scheduler.snapshot(key_for(), RECIPIENT)["attempts"] == 0


def test_concurrent_claims_publish_once(gate):
    from concurrent.futures import ThreadPoolExecutor

    scheduler, _ledger = gate
    scheduler.schedule(observation(), RECIPIENT, now=NOW)
    calls = []

    def publish(payload, recipient):
        calls.append(recipient)
        return {"status": "tracked", "message_id": MESSAGE, "envelope_sha256": DIGEST}

    def run(_slot):
        return scheduler.advance(
            publish=publish, allowed_providers={"synthetic-retry"},
            policy=policy(), now=NOW, observed_clock=CLOCK,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    published = [item for batch in results for item in batch if item["state"] == "published"]
    assert len(published) == 1
    assert len(calls) == 1
