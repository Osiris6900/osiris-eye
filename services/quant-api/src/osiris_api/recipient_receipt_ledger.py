"""Durable receipt-correlation ledger; never equates local writes with remote admission.

Only verified receipt events from the Rust node may call acknowledge().
This ledger is intentionally not yet wired into automatic retransmission.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class RecipientReceiptLedger:
    def __init__(self, path: str):
        if not path or path == ":memory:":
            raise ValueError("durable_file_path_required")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS recipient_receipts (
                message_id TEXT NOT NULL,
                recipient_peer_id TEXT NOT NULL,
                envelope_sha256 TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending'
                    CHECK(state IN ('pending', 'acknowledged', 'unconfirmed')),
                created_at INTEGER NOT NULL,
                acknowledged_at INTEGER,
                PRIMARY KEY (message_id, recipient_peer_id)
            )""")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def register(self, message_id: str, recipient: str, digest: str, *,
                 now: int | None = None) -> bool:
        if not message_id or not recipient or len(digest) != 64 or any(
            c not in "0123456789abcdef" for c in digest
        ):
            raise ValueError("invalid_receipt_reference")
        with self._connect() as db:
            row = db.execute(
                "SELECT envelope_sha256 FROM recipient_receipts "
                "WHERE message_id=? AND recipient_peer_id=?",
                (message_id, recipient),
            ).fetchone()
            if row is not None:
                if row[0] != digest:
                    raise ValueError("receipt_reference_conflict")
                return False
            db.execute(
                "INSERT INTO recipient_receipts "
                "(message_id,recipient_peer_id,envelope_sha256,created_at) "
                "VALUES (?,?,?,?)",
                (message_id, recipient, digest, int(time.time()) if now is None else now),
            )
            return True

    def acknowledge_verified(self, message_id: str, recipient: str, digest: str, *,
                             verified: bool, now: int | None = None) -> bool:
        """Caller must authenticate signature, signer, sender, freshness and policy."""
        if not verified:
            raise ValueError("verified_receipt_required")
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE recipient_receipts SET state='acknowledged', acknowledged_at=? "
                "WHERE message_id=? AND recipient_peer_id=? AND envelope_sha256=? "
                "AND state IN ('pending','unconfirmed')",
                (int(time.time()) if now is None else now, message_id, recipient, digest),
            )
            return cursor.rowcount == 1

    def mark_unconfirmed(self, message_id: str, recipient: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE recipient_receipts SET state='unconfirmed' "
                "WHERE message_id=? AND recipient_peer_id=? AND state='pending'",
                (message_id, recipient),
            )
            return cursor.rowcount == 1

    def counts(self) -> dict[str, int]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT state,COUNT(*) FROM recipient_receipts GROUP BY state"
            ).fetchall()
        return {state: dict(rows).get(state, 0)
                for state in ("pending", "acknowledged", "unconfirmed")}
