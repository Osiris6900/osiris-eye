"""SQLite-backed append-only audit evidence with transactional hash chaining.

Append stamps the actor, timestamp, and hash links on the server. Caller
sequence, actor, and hash fields are not the chain. A signed checkpoint
written outside this database is what makes a rewrite from genesis detectable.
That check is ``evidence_status`` from ``audit_evidence.verify_evidence``.
This store is still not WORM storage.
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
        from .audit_evidence import stamp_server_event
        event = stamp_server_event(event)
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
        except sqlite3.IntegrityError as exc:
            db.rollback()
            raise ValueError("duplicate event_id or audit hash") from exc
        finally:
            db.close()
        return {
            "status": "appended", "sequence": sequence, "event_id": event_id,
            "previous_hash": previous_hash, "hash": digest,
            "actor": event["actor"], "actor_source": "server",
            "actor_authentication_verified": event["actor_authentication_verified"] is True,
            "checkpoint": self._refresh_anchor(sequence),
            "live_execution_enabled": False, "live_trading_authorized": False,
        }

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
        evidence = self._evidence_status()
        return {
            "status": "valid" if not issues else "tampered_or_invalid",
            "event_count": len(rows), "head_hash": previous_hash,
            "issue_count": len(issues), "issues": issues,
            "storage": "sqlite", "append_only_api": True, "durable_storage": True,
            "worm_or_privileged_rewrite_protection": evidence["status"] == "ok",
            "actor_authentication_verified": evidence["actor_authentication_verified"],
            "evidence_status": evidence["status"],
            "signature_status": evidence["signature_status"],
            "signed": evidence["signed"],
            "trusted_timestamp_verified": False,
            "new_risk_allowed": False, "live_execution_enabled": False,
            "live_trading_authorized": False,
        }

    def _refresh_anchor(self, sequence: int) -> str:
        try:
            from .audit_evidence import refresh_phase16_anchor
            return refresh_phase16_anchor(self, sequence)
        except (OSError, ValueError, TypeError):
            return "error"

    def _evidence_status(self) -> dict[str, Any]:
        try:
            from .audit_evidence import verify_evidence
            evidence = verify_evidence("phase16", database=self.database_path)
        except (OSError, ValueError, TypeError):
            return {"status": "unverifiable", "signature_status": "unsigned", "signed": False,
                    "actor_authentication_verified": False}
        return evidence
