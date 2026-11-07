"""Durable idempotent consumer for kernel-authenticated Rust receipt events.

This is not a network endpoint. ``consume`` accepts only the credential object
the Unix adapter constructs after SO_PEERCRED succeeds. A boolean, including
``True``, is not authentication and cannot acknowledge a receipt.
"""
from __future__ import annotations

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
            return APPLIED
