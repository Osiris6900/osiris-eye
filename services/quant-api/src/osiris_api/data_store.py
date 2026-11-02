"""Small durable SQLite store for normalized, point-in-time research observations.

Raw vendor payloads should be retained separately in immutable object storage in a
production deployment. This store is a local integration baseline, not a data lake.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601 with timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc).isoformat()


class ResearchStore:
    def __init__(self, database_path: str | None = None):
        self.database_path = database_path or os.getenv(
            "OSIRIS_DB_PATH", "data/osiris_research.sqlite3"
        )
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dataset TEXT NOT NULL,
                    record_key TEXT NOT NULL,
                    source TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    payload_json TEXT NOT NULL,
                    UNIQUE(dataset, source, record_key)
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_observations_dataset_time
                ON observations(dataset, available_at, event_time)
            """)

    def put(self, *, dataset: str, record_key: str, source: str,
            event_time: str, available_at: str, payload: dict[str, Any]) -> dict[str, Any]:
        dataset, record_key, source = dataset.strip(), record_key.strip(), source.strip()
        if not all((dataset, record_key, source)):
            raise ValueError("dataset, record_key and source must be non-empty")
        if len(dataset) > 100 or len(record_key) > 200 or len(source) > 200:
            raise ValueError("dataset, record_key or source exceeds length limit")
        event_time = _utc_timestamp(event_time)
        available_at = _utc_timestamp(available_at)
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        encoded = json.dumps(payload, allow_nan=False, separators=(",", ":"))
        with self._connect() as connection:
            cursor = connection.execute("""
                INSERT INTO observations
                    (dataset, record_key, source, event_time, available_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(dataset, source, record_key) DO UPDATE SET
                    event_time=excluded.event_time,
                    available_at=excluded.available_at,
                    payload_json=excluded.payload_json
            """, (dataset, record_key, source, event_time, available_at, encoded))
            row = connection.execute("""
                SELECT id, dataset, record_key, source, event_time, available_at,
                       ingested_at, payload_json
                FROM observations WHERE dataset=? AND source=? AND record_key=?
            """, (dataset, source, record_key)).fetchone()
        return self._serialize(row)

    @staticmethod
    def _serialize(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "dataset": row["dataset"], "record_key": row["record_key"],
            "source": row["source"], "event_time": row["event_time"],
            "available_at": row["available_at"], "ingested_at": row["ingested_at"],
            "payload": json.loads(row["payload_json"]),
        }

    def list(self, dataset: str, limit: int = 100) -> list[dict[str, Any]]:
        if not dataset.strip():
            raise ValueError("dataset must be non-empty")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT id, dataset, record_key, source, event_time, available_at,
                       ingested_at, payload_json
                FROM observations WHERE dataset=?
                ORDER BY available_at DESC, id DESC LIMIT ?
            """, (dataset, limit)).fetchall()
        return [self._serialize(row) for row in rows]

    def list_as_of(self, dataset: str, as_of: str, limit: int = 100) -> list[dict[str, Any]]:
        if not dataset.strip():
            raise ValueError("dataset must be non-empty")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        cutoff = _utc_timestamp(as_of)
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT id, dataset, record_key, source, event_time, available_at,
                       ingested_at, payload_json
                FROM observations
                WHERE dataset=? AND event_time<=? AND available_at<=?
                ORDER BY event_time DESC, available_at DESC, id DESC LIMIT ?
            """, (dataset, cutoff, cutoff, limit)).fetchall()
        return [self._serialize(row) for row in rows]
