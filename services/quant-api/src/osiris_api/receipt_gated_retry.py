"""Opt-in, disabled-by-default retry gate in front of the local observation writer.

This does not retransmit a tracked encrypted envelope. The node mints a new
message id and ciphertext on every stdin write, and the recipient replay cache
is keyed by that message id. After one tracked publication, the attempt waits
for the verified receipt ledger and then becomes acknowledged or unconfirmed.
It is not written again.

``OSIRIS_OUTBOX_RECEIPT_RETRY`` must be exactly ``1``. Any other value, including
unset, leaves the gate disabled. Disabled objects do not schedule or write.
A local stdin success is never recorded as recipient acknowledgement.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .p2p_ingestion import IngestionRejection, validate_p2p_observation
from .recipient_receipt_ledger import RecipientReceiptLedger

RETRY_ENV = "OSIRIS_OUTBOX_RECEIPT_RETRY"
MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 2
BACKOFF_CAP_SECONDS = 60
RECEIPT_WAIT_SECONDS = 120
MAX_CHANNEL_DEFERRALS = 8

Publish = Callable[[str, str], dict[str, object]]


class PublishNotSent(Exception):
    """The writer failed before the node could accept the observation line."""


class AdmissionDecision:
    """Explicit peer sets. Missing sets are uncertain and must not send."""

    def __init__(self, trusted: set[str] | None, revoked: set[str] | None):
        if trusted is not None and not isinstance(trusted, set):
            raise ValueError("invalid_admission_policy")
        if revoked is not None and not isinstance(revoked, set):
            raise ValueError("invalid_admission_policy")
        self.trusted = trusted
        self.revoked = revoked

    def outcome(self, recipient: str) -> str:
        if self.trusted is None or self.revoked is None:
            return "policy_uncertain"
        if recipient not in self.trusted:
            return "recipient_not_allowed"
        if recipient in self.revoked:
            return "recipient_revoked"
        return "allowed"


def backoff_seconds(failures: int) -> int:
    """Deterministic exponential base delay. ``failures`` is how many sends already failed."""
    if type(failures) is not int or failures < 1:
        raise ValueError("invalid_backoff_attempt")
    delay = BACKOFF_BASE_SECONDS * (2 ** (failures - 1))
    return min(BACKOFF_CAP_SECONDS, delay)


def jittered_backoff_seconds(
    failures: int, *, observation_key: str, recipient: str,
) -> int:
    """Deterministic half-jitter over :func:`backoff_seconds`.

    Without jitter every failing attempt for the same cohort wakes on the same
    second and hammers the node together (thundering herd). The spread must not
    come from a random source: this scheduler is durable, and a restart has to
    reproduce the same ``next_attempt_at`` arithmetic from what is on disk.

    The offset is therefore derived from SHA-256 of
    ``observation_key | recipient | failures`` — stable across restarts,
    distinct per attempt and per recipient, and uniform enough to spread a
    cohort across the upper half of the backoff band, ``[base // 2, base]``.
    The floor keeps a minimum wait so jitter never collapses into a hot loop.
    """
    if not isinstance(observation_key, str) or not isinstance(recipient, str):
        raise ValueError("invalid_jitter_material")
    base = backoff_seconds(failures)
    material = f"{observation_key}|{recipient}|{failures}".encode()
    digest = hashlib.sha256(material).digest()
    spread = base - base // 2
    return base // 2 + int.from_bytes(digest[:8], "big") % (spread + 1)


def _enabled_flag(enabled: bool | None) -> bool:
    if enabled is None:
        return os.environ.get(RETRY_ENV) == "1"
    if enabled is True or enabled is False:
        return enabled
    raise ValueError("invalid_retry_flag")


def _recipient_ok(recipient: str) -> bool:
    return (
        isinstance(recipient, str)
        and 1 <= len(recipient) <= 128
        and all(33 <= ord(char) <= 126 for char in recipient)
    )


class ReceiptRetryScheduler:
    def __init__(
        self,
        path: str,
        ledger: RecipientReceiptLedger,
        *,
        enabled: bool | None = None,
    ):
        if not path or path == ":memory:":
            raise ValueError("durable_file_path_required")
        self.path = path
        self.ledger = ledger
        self.enabled = _enabled_flag(enabled)
        if not self.enabled:
            return
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._immediate() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS receipt_retry_attempts (
                observation_key TEXT NOT NULL,
                recipient_peer_id TEXT NOT NULL,
                observation_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN (
                    'scheduled','publishing','published','acknowledged',
                    'unconfirmed','denied','expired','held'
                )),
                message_id TEXT,
                envelope_sha256 TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                deferrals INTEGER NOT NULL DEFAULT 0,
                next_attempt_at INTEGER NOT NULL,
                published_at INTEGER,
                reason TEXT,
                PRIMARY KEY (observation_key, recipient_peer_id)
            )""")
            # A crash after the claim and before a terminal state must not send again.
            db.execute(
                """UPDATE receipt_retry_attempts
                   SET state='held', reason='crash_while_publishing'
                   WHERE state='publishing'"""
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @contextmanager
    def _immediate(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
        except BaseException:
            db.execute("ROLLBACK")
            raise
        else:
            db.execute("COMMIT")
        finally:
            db.close()

    def _require_enabled(self) -> None:
        if self.enabled is not True:
            raise ValueError("receipt_retry_disabled")

    def schedule(self, observation: dict[str, Any], recipient: str, *, now: int) -> bool:
        """Persist one recipient attempt. A duplicate key does not reset state."""
        self._require_enabled()
        if type(now) is not int:
            raise ValueError("invalid_retry_clock")
        if not _recipient_ok(recipient):
            raise ValueError("invalid_recipient")
        from .p2p_ingestion import validate_observation_contract
        normalized = validate_observation_contract(observation)
        key = ":".join((
            normalized["provider"], normalized["instrument"], normalized["payload_sha256"],
        ))
        encoded = json.dumps(observation, separators=(",", ":"), sort_keys=True)
        with self._immediate() as db:
            cursor = db.execute(
                """INSERT INTO receipt_retry_attempts (
                    observation_key, recipient_peer_id, observation_json, state,
                    attempts, deferrals, next_attempt_at
                ) VALUES (?, ?, ?, 'scheduled', 0, 0, ?)
                ON CONFLICT(observation_key, recipient_peer_id) DO NOTHING""",
                (key, recipient, encoded, now),
            )
            return cursor.rowcount == 1

    def advance(
        self,
        *,
        publish: Publish,
        allowed_providers: set[str] | frozenset[str],
        policy: AdmissionDecision,
        now: int,
        channel_ready: bool = True,
        observed_clock: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, str]]:
        """Recheck policy and freshness, then write at most one new envelope per attempt."""
        self._require_enabled()
        if type(now) is not int or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("invalid_retry_clock")
        if not isinstance(policy, AdmissionDecision):
            raise TypeError("invalid_admission_policy")
        if channel_ready is not True and channel_ready is not False:
            raise ValueError("invalid_channel_state")
        clock = observed_clock or datetime.fromtimestamp(now, UTC)
        if clock.tzinfo is None:
            raise ValueError("invalid_retry_clock")
        self._apply_ledger(now)
        if channel_ready is not True:
            return self._defer_channel(now, limit)
        outcomes = []
        with self._immediate() as db:
            due = db.execute(
                """SELECT observation_key, recipient_peer_id FROM receipt_retry_attempts
                   WHERE state='scheduled' AND next_attempt_at<=?
                   ORDER BY next_attempt_at, observation_key, recipient_peer_id
                   LIMIT ?""",
                (now, limit),
            ).fetchall()
        for key, recipient in due:
            outcomes.append(self._attempt(
                key, recipient, publish=publish, allowed_providers=allowed_providers,
                policy=policy, now=now, clock=clock,
            ))
        return outcomes

    def _apply_ledger(self, now: int) -> None:
        with self._immediate() as db:
            rows = db.execute(
                """SELECT observation_key, recipient_peer_id, message_id, envelope_sha256, published_at
                   FROM receipt_retry_attempts WHERE state='published'"""
            ).fetchall()
        for key, recipient, message_id, digest, published_at in rows:
            status = self.ledger.status(message_id, recipient, digest)
            if status == "acknowledged":
                self._set_state(key, recipient, "acknowledged", reason=None, expect="published")
                continue
            if published_at is not None and now >= published_at + RECEIPT_WAIT_SECONDS:
                self.ledger.mark_unconfirmed(message_id, recipient)
                self._set_state(
                    key, recipient, "unconfirmed", reason="receipt_wait_expired", expect="published",
                )

    def _defer_channel(self, now: int, limit: int) -> list[dict[str, str]]:
        outcomes = []
        with self._immediate() as db:
            rows = db.execute(
                """SELECT observation_key, recipient_peer_id, deferrals
                   FROM receipt_retry_attempts
                   WHERE state='scheduled' AND next_attempt_at<=?
                   ORDER BY next_attempt_at, observation_key, recipient_peer_id
                   LIMIT ?""",
                (now, limit),
            ).fetchall()
            for key, recipient, deferrals in rows:
                nxt = deferrals + 1
                if nxt > MAX_CHANNEL_DEFERRALS:
                    state, reason = "held", "receipt_channel_unavailable"
                    delay = now
                else:
                    state, reason = "scheduled", "receipt_channel_unavailable"
                    delay = now + jittered_backoff_seconds(
                        nxt, observation_key=key, recipient=recipient,
                    )
                db.execute(
                    """UPDATE receipt_retry_attempts
                       SET state=?, reason=?, deferrals=?, next_attempt_at=?
                       WHERE observation_key=? AND recipient_peer_id=? AND state='scheduled'""",
                    (state, reason, nxt, delay, key, recipient),
                )
                outcomes.append({
                    "observation_key": key, "recipient_peer_id": recipient,
                    "state": state, "reason": reason,
                })
        return outcomes

    def _attempt(
        self, key: str, recipient: str, *, publish: Publish,
        allowed_providers: set[str] | frozenset[str], policy: AdmissionDecision,
        now: int, clock: datetime,
    ) -> dict[str, str]:
        with self._immediate() as db:
            claimed = db.execute(
                """UPDATE receipt_retry_attempts SET state='publishing'
                   WHERE observation_key=? AND recipient_peer_id=?
                     AND state='scheduled' AND next_attempt_at<=?""",
                (key, recipient, now),
            ).rowcount
            if claimed != 1:
                row = self._row(db, key, recipient)
                return _view(row)
            row = self._row(db, key, recipient)
        decision = policy.outcome(recipient)
        if decision == "policy_uncertain":
            return self._reschedule(key, recipient, now, "policy_uncertain", consume_attempt=False)
        if decision != "allowed":
            return self._finish(key, recipient, "denied", decision)
        try:
            observation = json.loads(row["observation_json"])
            validate_p2p_observation(observation, allowed_providers=allowed_providers, now=clock)
        except json.JSONDecodeError:
            return self._finish(key, recipient, "held", "invalid_observation_json")
        except IngestionRejection as exc:
            reason = str(exc)
            if reason in {"observation_stale", "provider_not_authorized"}:
                state = "expired" if reason == "observation_stale" else "denied"
                return self._finish(key, recipient, state, reason)
            if reason in {"observation_future_dated", "ingestion_disabled_kill_switch"}:
                return self._reschedule(key, recipient, now, reason, consume_attempt=False)
            return self._finish(key, recipient, "denied", reason)
        try:
            result = publish(row["observation_json"], recipient)
        except PublishNotSent:
            return self._reschedule(key, recipient, now, "publish_not_sent", consume_attempt=True)
        except Exception:  # noqa: BLE001 - ambiguous writes must not be retried
            return self._finish(key, recipient, "held", "publish_result_uncertain")
        try:
            status, message_id, digest = _correlation(result)
        except ValueError:
            return self._finish(key, recipient, "held", "invalid_publish_correlation")
        if status != "tracked":
            return self._reschedule(key, recipient, now, status, consume_attempt=True)
        try:
            self.ledger.register(message_id, recipient, digest, now=now)
        except (OSError, ValueError, sqlite3.Error):
            return self._finish(key, recipient, "held", "correlation_not_durable")
        with self._immediate() as db:
            db.execute(
                """UPDATE receipt_retry_attempts
                   SET state='published', message_id=?, envelope_sha256=?, published_at=?, reason=NULL
                   WHERE observation_key=? AND recipient_peer_id=? AND state='publishing'""",
                (message_id, digest, now, key, recipient),
            )
        return {
            "observation_key": key, "recipient_peer_id": recipient,
            "state": "published", "reason": "",
        }

    def _reschedule(
        self, key: str, recipient: str, now: int, reason: str, *, consume_attempt: bool,
    ) -> dict[str, str]:
        with self._immediate() as db:
            row = self._row(db, key, recipient)
            attempts = row["attempts"] + (1 if consume_attempt else 0)
            if consume_attempt and attempts >= MAX_ATTEMPTS:
                db.execute(
                    """UPDATE receipt_retry_attempts
                       SET state='held', reason='retry_budget_exhausted', attempts=?
                       WHERE observation_key=? AND recipient_peer_id=? AND state='publishing'""",
                    (attempts, key, recipient),
                )
                return {
                    "observation_key": key, "recipient_peer_id": recipient,
                    "state": "held", "reason": "retry_budget_exhausted",
                }
            delay = now + jittered_backoff_seconds(
                attempts if consume_attempt else 1,
                observation_key=key, recipient=recipient,
            )
            db.execute(
                """UPDATE receipt_retry_attempts
                   SET state='scheduled', attempts=?, next_attempt_at=?, reason=?
                   WHERE observation_key=? AND recipient_peer_id=? AND state='publishing'""",
                (attempts, delay, reason, key, recipient),
            )
        return {
            "observation_key": key, "recipient_peer_id": recipient,
            "state": "scheduled", "reason": reason,
        }

    def _finish(self, key: str, recipient: str, state: str, reason: str) -> dict[str, str]:
        self._set_state(key, recipient, state, reason=reason, expect="publishing")
        return {
            "observation_key": key, "recipient_peer_id": recipient,
            "state": state, "reason": reason,
        }

    def _set_state(
        self, key: str, recipient: str, state: str, *, reason: str | None, expect: str,
    ) -> None:
        with self._immediate() as db:
            db.execute(
                """UPDATE receipt_retry_attempts SET state=?, reason=?
                   WHERE observation_key=? AND recipient_peer_id=? AND state=?""",
                (state, reason, key, recipient, expect),
            )

    def _row(self, db: sqlite3.Connection, key: str, recipient: str) -> dict[str, Any]:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT * FROM receipt_retry_attempts WHERE observation_key=? AND recipient_peer_id=?",
            (key, recipient),
        ).fetchone()
        if row is None:
            raise ValueError("retry_attempt_missing")
        return dict(row)

    def snapshot(self, observation_key: str, recipient: str) -> dict[str, Any] | None:
        self._require_enabled()
        with self._immediate() as db:
            try:
                return self._row(db, observation_key, recipient)
            except ValueError:
                return None

    def counts(self) -> dict[str, int]:
        self._require_enabled()
        states = (
            "scheduled", "publishing", "published", "acknowledged",
            "unconfirmed", "denied", "expired", "held",
        )
        with self._immediate() as db:
            rows = db.execute(
                "SELECT state, COUNT(*) FROM receipt_retry_attempts GROUP BY state"
            ).fetchall()
        found = dict(rows)
        return {state: found.get(state, 0) for state in states}


def _correlation(result: object) -> tuple[str, str | None, str | None]:
    if not isinstance(result, dict) or result.get("status") not in {"tracked", "rejected", "deferred"}:
        raise ValueError("invalid_publish_result")
    status = str(result["status"])
    if status != "tracked":
        return status, None, None
    message_id = result.get("message_id")
    digest = result.get("envelope_sha256")
    if (not isinstance(message_id, str) or not 1 <= len(message_id) <= 128
            or any(ord(char) < 33 or ord(char) > 126 for char in message_id)):
        raise ValueError("invalid_publish_correlation")
    if (not isinstance(digest, str) or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)):
        raise ValueError("invalid_publish_correlation")
    return status, message_id, digest


def _view(row: dict[str, Any]) -> dict[str, str]:
    return {
        "observation_key": row["observation_key"],
        "recipient_peer_id": row["recipient_peer_id"],
        "state": row["state"],
        "reason": row["reason"] or "",
    }
