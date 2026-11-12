"""Durable idempotent consumer for kernel-authenticated Rust receipt events.

This is not a network endpoint. ``consume`` accepts only the credential object
the Unix adapter constructs after SO_PEERCRED succeeds. A boolean, including
``True``, is not authentication and cannot acknowledge a receipt.
"""
from __future__ import annotations

import sqlite3

from .recipient_receipt_ledger import RecipientReceiptLedger
from .verified_receipt_event import APPLIED, DUPLICATE, parse_event_envelope


class VerifiedReceiptConsumer:
    def __init__(self, ledger: RecipientReceiptLedger, *, sender_peer_id: str):
        if not isinstance(sender_peer_id, str) or not sender_peer_id:
            raise ValueError("sender_peer_id_required")
        self.ledger = ledger
        self.sender_peer_id = sender_peer_id
        with self.ledger._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS verified_receipt_events (
                event_id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                recipient_peer_id TEXT NOT NULL,
                envelope_sha256 TEXT NOT NULL,
                accepted_at INTEGER NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS verified_receipt_origins (
                event_id TEXT PRIMARY KEY,
                sender_peer_id TEXT NOT NULL,
                rust_pid INTEGER NOT NULL,
                rust_uid INTEGER NOT NULL
            )""")

    def verified_event(self, message_id: str, recipient: str, digest: str) -> dict | None:
        """Read an exact acknowledged event with persisted sender/process provenance.

        Historical events without an origin remain valid delivery history but
        cannot supply this stronger trace until authenticated redelivery.
        """
        with self.ledger._connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT e.*,o.sender_peer_id,o.rust_pid,o.rust_uid "
                "FROM verified_receipt_events e JOIN verified_receipt_origins o USING(event_id) "
                "JOIN recipient_receipts r ON r.message_id=e.message_id "
                "AND r.recipient_peer_id=e.recipient_peer_id "
                "AND r.envelope_sha256=e.envelope_sha256 "
                "WHERE e.message_id=? AND e.recipient_peer_id=? AND e.envelope_sha256=? "
                "AND o.sender_peer_id=? AND r.state='acknowledged'",
                (message_id, recipient, digest, self.sender_peer_id),
            ).fetchone()
        return dict(row) if row else None

    def consume(self, event: dict, *, peer_credentials: object, now: int) -> str:
        """Commit one correlated event, or recognize an identical durable replay.

        Returns ``applied`` when pending/unconfirmed becomes acknowledged.
        Returns ``duplicate`` when this exact event id was already committed;
        that does not change delivery state. Mismatches raise and roll back.
        The caller must write a transport ack only after this method returns.
        """
        from .verified_receipt_unix import LinuxPeerCredentials
        if type(peer_credentials) is not LinuxPeerCredentials:
            raise ValueError("unauthenticated_receipt_event")
        event_id, message_id, recipient, digest = parse_event_envelope(
            event,
            expected_sender_peer_id=self.sender_peer_id,
            now=now,
        )
        with self.ledger._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT message_id,recipient_peer_id,envelope_sha256,accepted_at "
                "FROM verified_receipt_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            identity = (message_id, recipient, digest, event["accepted_at"])
            if previous is not None:
                if previous != identity:
                    raise ValueError("receipt_event_id_conflict")
                self._record_origin(db, event_id, peer_credentials)
                return DUPLICATE
            current = db.execute(
                "SELECT envelope_sha256,state FROM recipient_receipts "
                "WHERE message_id=? AND recipient_peer_id=?",
                (message_id, recipient),
            ).fetchone()
            if current is None:
                raise ValueError("receipt_event_not_pending")
            if current[0] != digest:
                raise ValueError("receipt_digest_mismatch")
            cursor = db.execute(
                "UPDATE recipient_receipts SET state='acknowledged',acknowledged_at=? "
                "WHERE message_id=? AND recipient_peer_id=? AND envelope_sha256=? "
                "AND state IN ('pending','unconfirmed')",
                (now, message_id, recipient, digest),
            )
            if cursor.rowcount != 1:
                raise ValueError("receipt_event_not_pending")
            db.execute(
                "INSERT INTO verified_receipt_events "
                "(event_id,message_id,recipient_peer_id,envelope_sha256,accepted_at) "
                "VALUES (?,?,?,?,?)",
                (event_id, *identity),
            )
            self._record_origin(db, event_id, peer_credentials)
            return APPLIED

    def _record_origin(self, db, event_id, credentials):
        prior = db.execute(
            "SELECT sender_peer_id FROM verified_receipt_origins WHERE event_id=?", (event_id,)
        ).fetchone()
        if prior is not None and prior[0] != self.sender_peer_id:
            raise ValueError("receipt_event_sender_conflict")
        db.execute(
            "INSERT OR IGNORE INTO verified_receipt_origins VALUES (?,?,?,?)",
            (event_id, self.sender_peer_id, credentials.pid, credentials.uid),
        )
