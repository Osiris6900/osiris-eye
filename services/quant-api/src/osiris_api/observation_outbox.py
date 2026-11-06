"""Durable, explicitly drained SQLite outbox for validated mesh observations.

The caller must first pass the existing ingestion gates. A successful stdin write
only acknowledges delivery to the local node process, not remote receipt.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from .p2p_ingestion import ENABLE_ENV, IngestionRejection, validate_p2p_observation


class ObservationOutbox:
    def __init__(self, path: str):
        if path == ":memory:":
            raise ValueError("durable_file_path_required")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS pending_observations (
                observation_key TEXT PRIMARY KEY,
                observation_json TEXT NOT NULL,
                delivered INTEGER NOT NULL DEFAULT 0,
                rejected_reason TEXT
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(pending_observations)")}
            if "rejected_reason" not in columns:
                db.execute("ALTER TABLE pending_observations ADD COLUMN rejected_reason TEXT")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def enqueue(self, observation: dict[str, Any]) -> bool:
        from .p2p_ingestion import validate_observation_contract
        valid = validate_observation_contract(observation)
        key = ":".join((valid["provider"], valid["instrument"],
                        valid["payload_sha256"]))
        encoded = json.dumps(observation, separators=(",", ":"), sort_keys=True)
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO pending_observations "
                "(observation_key, observation_json) VALUES (?, ?)",
                (key, encoded),
            )
            return cursor.rowcount == 1

    def drain(
        self,
        write_line: Callable[[str], None],
        *,
        allowed_providers: Iterable[str],
        now: datetime | None = None,
        limit: int = 100,
    ) -> int:
        """Retry under current admission policy, quarantining invalid records.

        Disabled ingestion and missing provider configuration pause the queue;
        neither condition discards previously accepted observations. Records
        that no longer pass validation remain on disk with a rejection reason.
        ``limit`` bounds rows examined, including quarantined observations.
        """
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("invalid_drain_limit")
        if os.getenv(ENABLE_ENV) != "1":
            raise IngestionRejection("ingestion_disabled_kill_switch")
        providers = {str(item).strip() for item in allowed_providers if str(item).strip()}
        if not providers:
            raise IngestionRejection("no_authorized_providers")
        count = 0
        with self._connect() as db:
            rows = db.execute(
                "SELECT observation_key, observation_json FROM pending_observations "
                "WHERE delivered=0 AND rejected_reason IS NULL ORDER BY rowid LIMIT ?",
                (limit,),
            ).fetchall()
        for key, line in rows:
            # Recheck even when a previous writer changes the switch mid-drain.
            if os.getenv(ENABLE_ENV) != "1":
                break
            try:
                observation = json.loads(line)
                validate_p2p_observation(
                    observation, allowed_providers=providers, now=now
                )
            except (json.JSONDecodeError, IngestionRejection) as exc:
                if isinstance(exc, IngestionRejection):
                    if str(exc) == "ingestion_disabled_kill_switch":
                        break
                    if str(exc) == "no_authorized_providers":
                        raise
                reason = (
                    "invalid_observation_json"
                    if isinstance(exc, json.JSONDecodeError) else str(exc)
                )
                with self._connect() as db:
                    db.execute(
                        "UPDATE pending_observations SET rejected_reason=? "
                        "WHERE observation_key=? AND delivered=0 AND rejected_reason IS NULL",
                        (reason, key),
                    )
                continue
            # A failed write leaves this record pending for the next drain.
            write_line(line)
            with self._connect() as db:
                db.execute(
                    "UPDATE pending_observations SET delivered=1 "
                    "WHERE observation_key=? AND delivered=0 AND rejected_reason IS NULL", (key,)
                )
            count += 1
        return count

    def pending_count(self) -> int:
        with self._connect() as db:
            return db.execute(
                "SELECT COUNT(*) FROM pending_observations "
                "WHERE delivered=0 AND rejected_reason IS NULL"
            ).fetchone()[0]

    def rejected_count(self) -> int:
        with self._connect() as db:
            return db.execute(
                "SELECT COUNT(*) FROM pending_observations WHERE rejected_reason IS NOT NULL"
            ).fetchone()[0]
