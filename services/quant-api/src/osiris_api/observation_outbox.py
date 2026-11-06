"""Durable, explicitly drained SQLite outbox for validated mesh observations.

The caller must first pass the existing ingestion gates. A successful stdin write
only acknowledges delivery to the local node process, not remote receipt.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable


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
                delivered INTEGER NOT NULL DEFAULT 0
            )""")

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

    def drain(self, write_line: Callable[[str], None], *, limit: int = 100) -> int:
        if not 1 <= limit <= 1000:
            raise ValueError("invalid_drain_limit")
        count = 0
        with self._connect() as db:
            rows = db.execute(
                "SELECT observation_key, observation_json FROM pending_observations "
                "WHERE delivered=0 ORDER BY rowid LIMIT ?", (limit,)
            ).fetchall()
        for key, line in rows:
            # A failed write leaves this record pending for the next drain.
            write_line(line)
            with self._connect() as db:
                db.execute(
                    "UPDATE pending_observations SET delivered=1 "
                    "WHERE observation_key=? AND delivered=0", (key,)
                )
            count += 1
        return count

    def pending_count(self) -> int:
        with self._connect() as db:
            return db.execute(
                "SELECT COUNT(*) FROM pending_observations WHERE delivered=0"
            ).fetchone()[0]
