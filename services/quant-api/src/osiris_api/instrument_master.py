"""Versioned SQLite instrument master with append-only metadata history."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .market_universe import normalize_instrument


def _timestamp(value: str | None) -> str:
    raw = value or datetime.now(timezone.utc).isoformat()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("effective_at_must_be_timezone_aware_iso8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("effective_at_must_be_timezone_aware_iso8601")
    return parsed.astimezone(timezone.utc).isoformat()


class InstrumentMasterStore:
    """Append-only versions; as-of reads select the latest effective version."""

    def __init__(self, database_path: str | None = None):
        self.database_path = database_path or os.getenv(
            "OSIRIS_INSTRUMENT_DB_PATH", "data/osiris_instruments.sqlite3"
        )
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS instrument_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    instrument_id TEXT NOT NULL,
                    effective_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    metadata_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    UNIQUE(instrument_id, effective_at)
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_instrument_versions_asof
                ON instrument_versions(instrument_id, effective_at DESC)
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS provider_instrument_mappings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    provider_symbol TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    effective_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    source TEXT NOT NULL,
                    UNIQUE(provider, provider_symbol, instrument_id, effective_at)
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_provider_mapping_lookup
                ON provider_instrument_mappings(provider, provider_symbol, effective_at DESC)
            """)

    def put(self, record: dict[str, Any], *, source: str = "operator",
            effective_at: str | None = None) -> dict[str, Any]:
        if not isinstance(source, str) or not source.strip() or len(source) > 200:
            raise ValueError("invalid_source")
        normalized = normalize_instrument(record)
        for field in ("expiry", "option_type", "strike", "base_currency",
                      "contract_multiplier"):
            if record.get(field) is not None and field not in normalized:
                normalized[field] = record[field]
        if normalized.get("option_type") is not None:
            option_type = str(normalized["option_type"]).upper()
            if option_type not in {"CALL", "PUT"}:
                raise ValueError("invalid_option_type")
            normalized["option_type"] = option_type
        if normalized.get("expiry") is not None:
            try:
                normalized["expiry"] = datetime.fromisoformat(
                    str(normalized["expiry"])
                ).date().isoformat()
            except ValueError as exc:
                raise ValueError("invalid_expiry") from exc
        if normalized.get("strike") is not None:
            from decimal import Decimal, InvalidOperation
            try:
                strike = Decimal(str(normalized["strike"]))
            except (InvalidOperation, ValueError):
                raise ValueError("invalid_strike") from None
            if not strike.is_finite() or strike <= 0:
                raise ValueError("invalid_strike")
            normalized["strike"] = format(strike.normalize(), "f")
        if normalized["asset_class"] == "option" and not all(
            normalized.get(key) is not None for key in ("expiry", "option_type", "strike")
        ):
            raise ValueError("option_requires_expiry_option_type_and_strike")
        effective = _timestamp(effective_at)
        encoded = json.dumps(normalized, allow_nan=False, sort_keys=True, separators=(",", ":"))
        with self._connect() as db:
            try:
                db.execute("""
                    INSERT INTO instrument_versions
                        (instrument_id, effective_at, metadata_json, source)
                    VALUES (?, ?, ?, ?)
                """, (normalized["instrument_id"], effective, encoded, source.strip()))
            except sqlite3.IntegrityError as exc:
                raise ValueError("instrument_version_already_exists") from exc
            row = db.execute("""
                SELECT id, instrument_id, effective_at, recorded_at, metadata_json, source
                FROM instrument_versions WHERE instrument_id=? AND effective_at=?
            """, (normalized["instrument_id"], effective)).fetchone()
        return self._serialize(row)

    @staticmethod
    def _serialize(row: sqlite3.Row) -> dict[str, Any]:
        return {"version_id": row["id"], "instrument_id": row["instrument_id"],
                "effective_at": row["effective_at"], "recorded_at": row["recorded_at"],
                "source": row["source"], "instrument": json.loads(row["metadata_json"])}

    def get(self, instrument_id: str, *, as_of: str | None = None) -> dict[str, Any] | None:
        if not isinstance(instrument_id, str) or not instrument_id.strip():
            raise ValueError("invalid_instrument_id")
        cutoff = _timestamp(as_of)
        with self._connect() as db:
            row = db.execute("""
                SELECT id, instrument_id, effective_at, recorded_at, metadata_json, source
                FROM instrument_versions
                WHERE instrument_id=? AND effective_at<=?
                ORDER BY effective_at DESC, id DESC LIMIT 1
            """, (instrument_id.strip(), cutoff)).fetchone()
        return self._serialize(row) if row else None

    def search(self, *, asset_class: str | None = None, venue: str | None = None,
               symbol: str | None = None, as_of: str | None = None,
               limit: int = 100) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit_must_be_between_1_and_1000")
        cutoff = _timestamp(as_of)
        clauses, params = [], [cutoff]
        for key, value in (("asset_class", asset_class), ("venue", venue), ("symbol", symbol)):
            if value is not None:
                if not isinstance(value, str) or not value.strip() or len(value) > 64:
                    raise ValueError(f"invalid_{key}")
                clauses.append(f"json_extract(metadata_json, '$.{key}') = ?")
                params.append(value.strip().upper() if key != "asset_class" else value.strip().lower())
        where = " AND ".join(["effective_at<=?", *clauses])
        params.append(limit)
        with self._connect() as db:
            rows = db.execute(f"""
                SELECT v.id, v.instrument_id, v.effective_at, v.recorded_at,
                       v.metadata_json, v.source
                FROM instrument_versions v
                WHERE {where} AND v.id = (
                    SELECT v2.id FROM instrument_versions v2
                    WHERE v2.instrument_id=v.instrument_id AND v2.effective_at<=?
                    ORDER BY v2.effective_at DESC, v2.id DESC LIMIT 1
                )
                ORDER BY v.instrument_id LIMIT ?
            """, [*params[:-1], cutoff, limit]).fetchall()
        return [self._serialize(row) for row in rows]

    def put_mapping(self, *, provider: str, provider_symbol: str,
                    instrument_id: str, source: str = "operator",
                    effective_at: str | None = None) -> dict[str, Any]:
        values = (provider, provider_symbol, instrument_id, source)
        if any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in values):
            raise ValueError("invalid_provider_mapping")
        effective = _timestamp(effective_at)
        with self._connect() as db:
            exists = db.execute("""
                SELECT 1 FROM instrument_versions WHERE instrument_id=? AND effective_at<=?
                LIMIT 1
            """, (instrument_id.strip(), effective)).fetchone()
            if not exists:
                raise ValueError("canonical_instrument_not_found_at_effective_time")
            try:
                db.execute("""
                    INSERT INTO provider_instrument_mappings
                        (provider, provider_symbol, instrument_id, effective_at, source)
                    VALUES (?, ?, ?, ?, ?)
                """, (provider.strip(), provider_symbol.strip(), instrument_id.strip(),
                      effective, source.strip()))
            except sqlite3.IntegrityError as exc:
                raise ValueError("provider_mapping_already_exists") from exc
        return {"provider": provider.strip(), "provider_symbol": provider_symbol.strip(),
                "instrument_id": instrument_id.strip(), "effective_at": effective,
                "source": source.strip()}

    def resolve(self, provider: str, provider_symbol: str,
                *, as_of: str | None = None) -> str | None:
        cutoff = _timestamp(as_of)
        with self._connect() as db:
            row = db.execute("""
                SELECT instrument_id FROM provider_instrument_mappings
                WHERE provider=? AND provider_symbol=? AND effective_at<=?
                ORDER BY effective_at DESC, id DESC LIMIT 1
            """, (provider, provider_symbol, cutoff)).fetchone()
        return row["instrument_id"] if row else None
