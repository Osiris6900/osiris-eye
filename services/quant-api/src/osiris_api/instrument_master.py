"""Versioned SQLite instrument master with append-only metadata history.

The checked-in fixture is the only seed. Nothing in this module fetches a
vendor security master or authorizes a live order.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone
from decimal import Context, Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .market_universe import _positive_decimal, normalize_instrument

_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()
_STATUSES = frozenset({"active", "halted", "delisted"})
_ACTION_TYPES = frozenset({"split", "cash_dividend", "symbol_change"})
_SYMBOL_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/-")


def _path_lock(path: str) -> threading.RLock:
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(path)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[path] = lock
        return lock


def _canonical(payload: Any) -> str:
    return json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _content_hash(payload: dict[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "content_hash"}
    return hashlib.sha256(_canonical(body).encode()).hexdigest()


def _scale(value: Decimal) -> int:
    text = format(value, "f")
    if "." not in text:
        return 0
    return len(text.rstrip("0").split(".")[1])


def _precision(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1000:
        raise ValueError(f"invalid_{field}")
    return value


def _on_grid(value: Decimal, step: Decimal) -> bool:
    if step <= 0:
        return False
    digits = len(value.as_tuple().digits) + len(step.as_tuple().digits) + 32
    with localcontext(Context(prec=max(4096, digits))):
        quotient = value / step
        return quotient == quotient.to_integral_value()


def _trading_hours(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("invalid_trading_hours")
    open_at = value.get("open")
    close_at = value.get("close")
    zone = value.get("timezone")
    if not isinstance(open_at, str) or not isinstance(close_at, str) or not isinstance(zone, str):
        raise ValueError("invalid_trading_hours")
    for clock in (open_at, close_at):
        parts = clock.split(":")
        if len(parts) != 2 or not all(part.isdigit() and len(part) == 2 for part in parts):
            raise ValueError("invalid_trading_hours")
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("invalid_trading_hours")
    try:
        ZoneInfo(zone)
    except ZoneInfoNotFoundError:
        raise ValueError("invalid_trading_hours") from None
    if open_at >= close_at:
        raise ValueError("invalid_trading_hours")
    return {"open": open_at, "close": close_at, "timezone": zone}


def _contract_terms(record: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
    status = record.get("status", "active")
    if not isinstance(status, str) or status.strip().lower() not in _STATUSES:
        raise ValueError("invalid_instrument_status")
    exchange = record.get("exchange", normalized["venue"])
    if not isinstance(exchange, str) or exchange.strip().upper() != normalized["venue"]:
        raise ValueError("invalid_exchange")
    currency = record.get("currency", normalized["quote_currency"])
    if not isinstance(currency, str) or currency.strip().upper() != normalized["quote_currency"]:
        raise ValueError("invalid_currency")
    min_qty = _positive_decimal(record.get("min_qty", normalized["lot_size"]), "min_qty")
    lot = Decimal(normalized["lot_size"])
    minimum = Decimal(min_qty)
    if minimum < lot or not _on_grid(minimum, lot):
        raise ValueError("invalid_min_qty")
    tick = Decimal(normalized["tick_size"])
    price_precision = _precision(
        record.get("price_precision", _scale(tick)), "price_precision",
    )
    qty_precision = _precision(
        record.get("qty_precision", _scale(lot)), "qty_precision",
    )
    terms: dict[str, Any] = {
        "exchange": normalized["venue"],
        "currency": normalized["quote_currency"],
        "status": status.strip().lower(),
        "min_qty": min_qty,
        "price_precision": price_precision,
        "qty_precision": qty_precision,
    }
    if record.get("effective_to") is not None:
        terms["effective_to"] = record["effective_to"]
    if record.get("trading_hours") is not None:
        terms["trading_hours"] = _trading_hours(record["trading_hours"])
    return terms


def fixture_path() -> Path:
    override = os.getenv("OSIRIS_INSTRUMENT_FIXTURE")
    if isinstance(override, str) and override.strip():
        return Path(override.strip())
    return Path(__file__).with_name("instrument_master_fixture.json")


def default_instrument_cache_path() -> str:
    return str(Path(tempfile.gettempdir()) / "osiris-eye-instrument-master.sqlite3")


def instrument_db_path() -> str:
    """Paper and read APIs share one catalog file. A memory path is not used."""
    explicit = os.getenv("OSIRIS_INSTRUMENT_DB_PATH")
    if isinstance(explicit, str) and explicit.strip() and explicit.strip() != ":memory:":
        return explicit.strip()
    paper = os.getenv("OSIRIS_PAPER_EXECUTION_DB")
    if isinstance(paper, str) and paper.strip() and paper.strip() != ":memory:":
        paper_path = Path(paper.strip())
        return str(paper_path.with_name(paper_path.stem + ".instruments.sqlite3"))
    return default_instrument_cache_path()


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
            db.execute("""
                CREATE TABLE IF NOT EXISTS corporate_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    instrument_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    effective_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    source TEXT NOT NULL,
                    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(instrument_id, action_type, effective_at)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS catalog_meta (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    fixture_sha256 TEXT NOT NULL,
                    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def put(self, record: dict[str, Any], *, source: str = "operator",
            effective_at: str | None = None) -> dict[str, Any]:
        with _path_lock(self.database_path):
            return self._put(record, source=source, effective_at=effective_at)

    def _put(self, record: dict[str, Any], *, source: str = "operator",
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
        normalized.update(_contract_terms(record, normalized))
        effective = _timestamp(effective_at)
        normalized["effective_from"] = effective
        if normalized.get("effective_to") is not None:
            try:
                effective_to = _timestamp(str(normalized["effective_to"]))
            except ValueError as exc:
                raise ValueError("invalid_effective_to") from exc
            if effective_to <= effective:
                raise ValueError("effective_to_must_follow_effective_from")
            normalized["effective_to"] = effective_to
        normalized["content_hash"] = _content_hash(normalized)
        encoded = _canonical(normalized)
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

    def list_master(self) -> list[dict[str, Any]]:
        """Latest stored version of every instrument, including future and inactive ones."""
        with self._connect() as db:
            rows = db.execute("""
                SELECT v.id, v.instrument_id, v.effective_at, v.recorded_at,
                       v.metadata_json, v.source
                FROM instrument_versions v
                WHERE v.id = (
                    SELECT v2.id FROM instrument_versions v2
                    WHERE v2.instrument_id = v.instrument_id
                    ORDER BY v2.effective_at DESC, v2.id DESC LIMIT 1
                )
                ORDER BY v.instrument_id
            """).fetchall()
        return [self._serialize(row) for row in rows]

    def catalog_version(self) -> str:
        """Stable hash of every stored version and corporate action. Not a row id."""
        with self._connect() as db:
            rows = db.execute("""
                SELECT instrument_id, effective_at, metadata_json
                FROM instrument_versions
                ORDER BY instrument_id, effective_at, id
            """).fetchall()
            actions = db.execute("""
                SELECT instrument_id, action_type, effective_at, content_hash
                FROM corporate_actions
                ORDER BY instrument_id, action_type, effective_at, id
            """).fetchall()
        instruments = []
        for row in rows:
            metadata = json.loads(row["metadata_json"])
            instruments.append({
                "instrument_id": row["instrument_id"],
                "effective_at": row["effective_at"],
                "content_hash": metadata.get("content_hash") or _content_hash(metadata),
            })
        payload = {
            "instruments": instruments,
            "corporate_actions": [dict(row) for row in actions],
        }
        return hashlib.sha256(_canonical(payload).encode()).hexdigest()

    def _meta_hash(self) -> str | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT fixture_sha256 FROM catalog_meta WHERE id=1"
            ).fetchone()
        return None if row is None else str(row["fixture_sha256"])

    def _instrument_count(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM instrument_versions").fetchone()[0])

    def ensure_seeded(self, fixture: Path | None = None) -> str:
        """Load the checked-in fixture into an empty catalog. Do not fetch a vendor."""
        path = fixture or fixture_path()
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        with _path_lock(self.database_path):
            current = self._meta_hash()
            if current == digest:
                return self.catalog_version()
            ephemeral = self.database_path == default_instrument_cache_path()
            if current is not None and not ephemeral:
                return self.catalog_version()
            if current is None and self._instrument_count() and not ephemeral:
                return self.catalog_version()
            if ephemeral and (current is not None or self._instrument_count()):
                with self._connect() as db:
                    db.execute("DELETE FROM instrument_versions")
                    db.execute("DELETE FROM provider_instrument_mappings")
                    db.execute("DELETE FROM corporate_actions")
                    db.execute("DELETE FROM catalog_meta")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("invalid_instrument_fixture")
            instruments = payload.get("instruments")
            actions = payload.get("corporate_actions", [])
            if not isinstance(instruments, list) or not instruments or not isinstance(actions, list):
                raise ValueError("invalid_instrument_fixture")
            source = payload.get("source", "checked-in-fixture")
            for item in instruments:
                if not isinstance(item, dict):
                    raise ValueError("invalid_instrument_fixture")
                try:
                    self._put(item, source=str(source), effective_at=item.get("effective_from"))
                except ValueError as exc:
                    if "already_exists" not in str(exc):
                        raise
            for action in actions:
                if not isinstance(action, dict):
                    raise ValueError("invalid_instrument_fixture")
                try:
                    self._put_corporate_action(action, source=str(source))
                except ValueError as exc:
                    if "already_exists" not in str(exc):
                        raise
            with self._connect() as db:
                db.execute(
                    "INSERT INTO catalog_meta(id, fixture_sha256) VALUES (1, ?) "
                    "ON CONFLICT(id) DO UPDATE SET fixture_sha256=excluded.fixture_sha256",
                    (digest,),
                )
            return self.catalog_version()

    def put_corporate_action(self, record: dict[str, Any], *, source: str = "operator") -> dict[str, Any]:
        with _path_lock(self.database_path):
            return self._put_corporate_action(record, source=source)

    def _put_corporate_action(self, record: dict[str, Any], *, source: str) -> dict[str, Any]:
        if not isinstance(record, dict):
            raise ValueError("invalid_corporate_action")
        if not isinstance(source, str) or not source.strip() or len(source) > 200:
            raise ValueError("invalid_source")
        action_type = record.get("action_type", record.get("type"))
        if action_type not in _ACTION_TYPES:
            raise ValueError("invalid_corporate_action")
        instrument_id = record.get("instrument_id")
        if not isinstance(instrument_id, str) or not instrument_id.strip() or len(instrument_id) > 200:
            raise ValueError("invalid_corporate_action")
        effective = _timestamp(record.get("effective_at"))
        payload: dict[str, Any] = {
            "instrument_id": instrument_id.strip(),
            "action_type": action_type,
            "effective_at": effective,
            "applied_to_positions": False,
        }
        if action_type == "split":
            payload["ratio"] = _positive_decimal(record.get("ratio"), "ratio")
        elif action_type == "cash_dividend":
            payload["cash_per_share"] = _positive_decimal(
                record.get("cash_per_share"), "cash_per_share",
            )
        else:
            new_symbol = record.get("new_symbol")
            if not isinstance(new_symbol, str) or not new_symbol.strip() or len(new_symbol) > 64:
                raise ValueError("invalid_corporate_action")
            payload["new_symbol"] = new_symbol.strip().upper()
        with self._connect() as db:
            exists = db.execute("""
                SELECT 1 FROM instrument_versions WHERE instrument_id=? AND effective_at<=?
                LIMIT 1
            """, (payload["instrument_id"], effective)).fetchone()
            if not exists:
                raise ValueError("canonical_instrument_not_found_at_effective_time")
            content_hash = _content_hash(payload)
            try:
                db.execute("""
                    INSERT INTO corporate_actions
                        (instrument_id, action_type, effective_at, payload_json, content_hash, source)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    payload["instrument_id"], action_type, effective, _canonical(payload),
                    content_hash, source.strip(),
                ))
            except sqlite3.IntegrityError as exc:
                raise ValueError("corporate_action_already_exists") from exc
        return {**payload, "content_hash": content_hash, "source": source.strip()}

    def list_corporate_actions(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("""
                SELECT payload_json, content_hash, source
                FROM corporate_actions
                ORDER BY instrument_id, action_type, effective_at, id
            """).fetchall()
        actions = []
        for row in rows:
            item = json.loads(row["payload_json"])
            item["content_hash"] = row["content_hash"]
            item["source"] = row["source"]
            item["applied_to_positions"] = False
            actions.append(item)
        return actions

    def _versions_for_symbol(self, symbol: str) -> list[sqlite3.Row]:
        with self._connect() as db:
            return db.execute("""
                SELECT id, instrument_id, effective_at, recorded_at, metadata_json, source
                FROM instrument_versions
                WHERE json_extract(metadata_json, '$.symbol') = ?
                ORDER BY instrument_id, effective_at, id
            """, (symbol,)).fetchall()

    def assess(self, symbol: Any, *, price: Any = None, quantity: Any = None,
               as_of: str | None = None) -> dict[str, Any]:
        """Fail closed. A missing, inactive, or off-grid order is not tradable."""
        catalog_version = self.catalog_version()
        base = {
            "ok": False,
            "reason": "unknown_instrument",
            "catalog_version": catalog_version,
            "instrument_id": None,
            "instrument_version": None,
            "symbol": None,
            "status": None,
        }
        if not isinstance(symbol, str):
            return base
        normalized = symbol.strip().upper()
        if (
            not normalized or len(normalized) > 64 or not normalized.isascii()
            or any(char not in _SYMBOL_CHARS for char in normalized)
        ):
            return base
        base["symbol"] = normalized
        try:
            cutoff = _timestamp(as_of)
        except ValueError:
            return base
        rows = self._versions_for_symbol(normalized)
        if not rows:
            return base
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(row["instrument_id"], []).append(row)
        eligible: list[tuple[sqlite3.Row, dict[str, Any]]] = []
        saw_future = False
        for versions in grouped.values():
            covering: tuple[sqlite3.Row, dict[str, Any]] | None = None
            future_only = True
            for row in versions:
                metadata = json.loads(row["metadata_json"])
                if row["effective_at"] > cutoff:
                    continue
                future_only = False
                end = metadata.get("effective_to")
                if end is not None and _timestamp(str(end)) <= cutoff:
                    continue
                covering = (row, metadata)
            if covering is not None:
                eligible.append(covering)
            elif future_only:
                saw_future = True
        if len(eligible) > 1:
            base["reason"] = "ambiguous_instrument"
            return base
        if not eligible:
            base["reason"] = (
                "instrument_not_yet_effective" if saw_future else "instrument_inactive"
            )
            return base
        row, metadata = eligible[0]
        base["instrument_id"] = row["instrument_id"]
        base["instrument_version"] = metadata.get("content_hash")
        status = metadata.get("status", "active")
        base["status"] = status
        if status != "active":
            base["reason"] = "instrument_inactive"
            return base
        tick = Decimal(metadata["tick_size"])
        lot = Decimal(metadata["lot_size"])
        price_precision = int(metadata.get("price_precision", _scale(tick)))
        qty_precision = int(metadata.get("qty_precision", _scale(lot)))
        minimum = Decimal(metadata.get("min_qty", metadata["lot_size"]))
        # Non-positive amounts stay with the numeric risk gate. The grid applies
        # only to a positive price or quantity the book could otherwise accept.
        price_amount = None if price is None else _amount(price)
        if price_amount is not None and (
            _scale(price_amount) > price_precision or not _on_grid(price_amount, tick)
        ):
            base["reason"] = "invalid_tick_size"
            return base
        quantity_amount = None if quantity is None else _amount(quantity)
        if quantity_amount is not None and (
            quantity_amount < minimum
            or _scale(quantity_amount) > qty_precision
            or not _on_grid(quantity_amount, lot)
        ):
            base["reason"] = "invalid_lot_size"
            return base
        base["ok"] = True
        base["reason"] = "tradable"
        return base


def _amount(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number <= 0:
        return None
    exponent = number.as_tuple().exponent
    if not isinstance(exponent, int) or abs(exponent) > 1000 or len(number.as_tuple().digits) > 1259:
        return None
    return number


def assess_order(symbol: Any, *, price: Any = None, quantity: Any = None,
                 as_of: str | None = None, database_path: str | None = None,
                 seed: bool = True) -> dict[str, Any]:
    store = InstrumentMasterStore(database_path or instrument_db_path())
    if seed:
        store.ensure_seeded()
    return store.assess(symbol, price=price, quantity=quantity, as_of=as_of)
