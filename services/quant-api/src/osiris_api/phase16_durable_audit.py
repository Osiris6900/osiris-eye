"""SQLite-backed append-only audit evidence with transactional hash chaining.

This provides local durable storage and tamper detection, not WORM storage,
cryptographic actor authentication, or protection against a privileged DB rewrite.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .phase15_audit_chain import _canonical_event


class DurableAuditStore:
    def __init__(self, database_path: str):
        self.database_path = database_path
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS audit_events (
                    sequence INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    previous_hash TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        return db

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(event, dict):
            raise ValueError("event must be an object")
        event_id = event.get("event_id")
        for field in ("event_id", "timestamp", "actor", "action"):
            value = event.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"event requires non-empty {field}")
        if "details" in event and not isinstance(event["details"], dict):
            raise ValueError("event details must be an object")
        encoded = _canonical_event(event).decode("utf-8")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT sequence, event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = 0 if previous is None else previous["sequence"] + 1
            previous_hash = "0" * 64 if previous is None else previous["event_hash"]
            digest = hashlib.sha256(_canonical_event({
                "sequence": sequence, "previous_hash": previous_hash, "event": event
            })).hexdigest()
            db.execute(
                "INSERT INTO audit_events(sequence,event_id,previous_hash,event_json,event_hash) VALUES(?,?,?,?,?)",
                (sequence, event_id, previous_hash, encoded, digest),
            )
            db.commit()
            return {"status": "appended", "sequence": sequence, "event_id": event_id,
                    "previous_hash": previous_hash, "hash": digest}
        except sqlite3.IntegrityError as exc:
            db.rollback()
            raise ValueError("duplicate event_id or audit hash") from exc
        finally:
            db.close()

    def verify(self) -> dict[str, Any]:
        with self._connect() as db:
            rows = db.execute("SELECT sequence,event_id,previous_hash,event_json,event_hash FROM audit_events ORDER BY sequence").fetchall()
        previous_hash = "0" * 64
        issues = []
        for expected_sequence, row in enumerate(rows):
            try:
                event = json.loads(row["event_json"])
                expected_hash = hashlib.sha256(_canonical_event({
                    "sequence": expected_sequence, "previous_hash": previous_hash, "event": event
                })).hexdigest()
            except (json.JSONDecodeError, ValueError, TypeError):
                issues.append({"sequence": expected_sequence, "code": "invalid_event_payload"})
                previous_hash = row["event_hash"]
                continue
            if row["sequence"] != expected_sequence:
                issues.append({"sequence": expected_sequence, "code": "sequence_mismatch"})
            if row["previous_hash"] != previous_hash:
                issues.append({"sequence": expected_sequence, "code": "previous_hash_mismatch"})
            if event.get("event_id") != row["event_id"]:
                issues.append({"sequence": expected_sequence, "code": "event_id_mismatch"})
            if row["event_hash"] != expected_hash:
                issues.append({"sequence": expected_sequence, "code": "hash_mismatch"})
            previous_hash = row["event_hash"]
        return {
            "status": "valid" if not issues else "tampered_or_invalid",
            "event_count": len(rows), "head_hash": previous_hash,
            "issue_count": len(issues), "issues": issues,
            "storage": "sqlite", "append_only_api": True,
            "worm_or_privileged_rewrite_protection": False,
            "actor_authentication_verified": False,
            "new_risk_allowed": False, "live_execution_enabled": False,
        }
