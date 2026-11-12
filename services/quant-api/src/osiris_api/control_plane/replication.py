"""Signed append-only event replication with per-origin ordering and idempotency.

This is deterministic event-log convergence, not consensus over account balances.
Trusted public keys are configured out of band; origin labels are not identities.
"""

from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .database import MAX_SQLITE_INTEGER, Database, canonical, digest, identifier


class Replicator:
    def __init__(self, db: Database, trusted: dict[str, bytes]):
        if len(trusted) > 100:
            raise ValueError("too_many_replication_origins")
        self.db = db
        self.keys = {
            identifier(origin): Ed25519PublicKey.from_public_bytes(key)
            for origin, key in trusted.items()
        }

    @staticmethod
    def sign(origin: str, seq: int, event_id: str, payload: object, key: Ed25519PrivateKey) -> dict:
        if type(seq) is not int or not 1 <= seq <= MAX_SQLITE_INTEGER:
            raise ValueError("invalid_sequence")
        event = {
            "origin": identifier(origin),
            "seq": seq,
            "id": identifier(event_id),
            "payload": payload,
        }
        return {
            **event,
            "digest": digest(event),
            "signature": base64.b64encode(key.sign(canonical(event))).decode(),
        }

    def apply(self, batch: list[dict]) -> dict:
        if not isinstance(batch, list) or not 1 <= len(batch) <= 1000:
            raise ValueError("invalid_replication_batch")
        canonical(batch)
        verified = []
        for item in batch:
            if not isinstance(item, dict) or set(item) != {
                "origin",
                "seq",
                "id",
                "payload",
                "digest",
                "signature",
            }:
                raise ValueError("invalid_replication_envelope")
            origin, eid = identifier(item["origin"]), identifier(item["id"])
            if (origin not in self.keys or type(item["seq"]) is not int
                    or not 1 <= item["seq"] <= MAX_SQLITE_INTEGER):
                raise ValueError("untrusted_replication_origin_or_sequence")
            event = {key: item[key] for key in ("origin", "seq", "id", "payload")}
            if digest(event) != item["digest"]:
                raise ValueError("replication_digest_mismatch")
            try:
                self.keys[origin].verify(
                    base64.b64decode(item["signature"], validate=True), canonical(event)
                )
            except Exception:
                raise ValueError("replication_signature_invalid") from None
            verified.append((origin, eid, item, canonical(event).decode()))
        inserted = 0
        with self.db.transaction() as conn:
            for origin, eid, item, body in verified:
                old = conn.execute(
                    "SELECT * FROM cp_events WHERE id=? OR (origin=? AND seq=?)",
                    (eid, origin, item["seq"]),
                ).fetchall()
                if old:
                    if (
                        len(old) != 1
                        or old[0]["body"] != body
                        or old[0]["digest"] != item["digest"]
                    ):
                        raise ValueError("replication_conflicting_event")
                    continue
                sequence = conn.execute(
                    "SELECT COALESCE(MAX(seq),0) FROM cp_events WHERE origin=?", (origin,)
                ).fetchone()[0]
                if item["seq"] != sequence + 1:
                    raise ValueError("replication_gap")
                conn.execute(
                    "INSERT INTO cp_events VALUES (?,?,?,?,?,?)",
                    (origin, item["seq"], eid, body, item["digest"], item["signature"]),
                )
                inserted += 1
        return {
            "inserted": inserted,
            "duplicates": len(batch) - inserted,
            "account_consensus_verified": False,
        }

    def export(self, origin: str, *, after: int = 0, limit: int = 100) -> list[dict]:
        if (type(after) is not int or not 0 <= after <= MAX_SQLITE_INTEGER
                or type(limit) is not int or not 1 <= limit <= 1000):
            raise ValueError("invalid_replication_cursor")
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM cp_events WHERE origin=? AND seq>? ORDER BY seq LIMIT ?",
                (identifier(origin), after, limit),
            ).fetchall()
        return [
            {**json.loads(row["body"]), "digest": row["digest"], "signature": row["signature"]}
            for row in rows
        ]

    def converged_state(self) -> dict:
        """A deterministic event-ID set; conflicting financial state is never netted."""
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT origin,seq,id,digest FROM cp_events ORDER BY origin,seq"
            ).fetchall()
        events = [dict(row) for row in rows]
        return {"event_count": len(events), "state_sha256": digest(events), "events": events}
