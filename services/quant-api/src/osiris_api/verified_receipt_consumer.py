"""Durable idempotent consumer for independently authenticated Rust receipt events.

This is NOT a network endpoint or an authentication mechanism. A trusted
transport adapter must establish the Rust peer identity independently before
calling consume(). Never derive authenticated_rust_peer from the event body.
"""
from __future__ import annotations

import sqlite3

from .recipient_receipt_ledger import RecipientReceiptLedger
from .verified_receipt_event import validate_trusted_event


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

    def consume(self, event: dict, *, authenticated_rust_peer: bool, now: int) -> bool:
        """Commit exact pending correlation and event ID in one SQLite transaction.

        Returns True only for a newly acknowledged correlation. Replaying an
        identical committed event returns False. An event ID reused for a
        different correlation is rejected. No retransmission is authorized.
        """
        event_id, message_id, recipient, digest = validate_trusted_event(
            event,
            authenticated_rust_peer=authenticated_rust_peer,
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
                return False
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
            return True
