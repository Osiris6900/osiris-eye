"""Versioned, provider-neutral exchange session calendar.

Calendar data is supplied by an operator or a trusted upstream source; this module
validates and stores it but does not claim official global holiday coverage.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def aware_utc(value: str | None) -> datetime:
    raw = datetime.now(timezone.utc).isoformat() if value is None else value
    if not isinstance(raw, str) or not raw:
        raise ValueError("timestamp_must_be_timezone_aware_iso8601")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamp_must_be_timezone_aware_iso8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp_must_be_timezone_aware_iso8601")
    return parsed.astimezone(timezone.utc)


def _local_datetime(day: date, clock: time, zone: ZoneInfo) -> datetime:
    if clock.tzinfo is not None:
        raise ValueError("session_times_must_be_local_wall_times")
    candidate = datetime.combine(day, clock).replace(tzinfo=zone)
    # Reject nonexistent local times (DST spring-forward gaps).
    roundtrip = candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
    if roundtrip != candidate.replace(tzinfo=None):
        raise ValueError("session_time_is_nonexistent_due_to_dst")
    if candidate.utcoffset() != candidate.replace(fold=1).utcoffset():
        raise ValueError("session_time_is_ambiguous_due_to_dst")
    return candidate


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("calendar_record_must_be_object")
    venue = record.get("venue")
    tz_name = record.get("timezone")
    source = record.get("source", "operator")
    if not isinstance(venue, str) or not venue.strip() or len(venue) > 64:
        raise ValueError("invalid_venue")
    if not isinstance(source, str) or not source.strip() or len(source) > 200:
        raise ValueError("invalid_source")
    try:
        zone = ZoneInfo(tz_name)
    except (TypeError, ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("invalid_iana_timezone") from exc
    try:
        day = date.fromisoformat(record.get("calendar_date", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("calendar_date_must_be_yyyy_mm_dd") from exc
    holiday = record.get("is_holiday", False)
    sessions = record.get("sessions", [])
    if not isinstance(holiday, bool) or not isinstance(sessions, list) or len(sessions) > 20:
        raise ValueError("invalid_holiday_or_sessions")
    if holiday and sessions:
        raise ValueError("holiday_cannot_contain_sessions")
    normalized = []
    intervals = []
    for item in sessions:
        if not isinstance(item, dict):
            raise ValueError("session_must_be_object")
        try:
            start = time.fromisoformat(item.get("open", ""))
            end = time.fromisoformat(item.get("close", ""))
        except (TypeError, ValueError) as exc:
            raise ValueError("session_open_close_must_be_hh_mm") from exc
        if start.second or start.microsecond or end.second or end.microsecond:
            raise ValueError("session_times_must_use_minute_precision")
        start_local = _local_datetime(day, start, zone)
        end_day = day + timedelta(days=1) if end <= start else day
        end_local = _local_datetime(end_day, end, zone)
        start_utc, end_utc = start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)
        if end_utc <= start_utc:
            raise ValueError("session_close_must_follow_open")
        intervals.append((start_utc, end_utc))
        normalized.append({"open": start.isoformat(timespec="minutes"),
                           "close": end.isoformat(timespec="minutes")})
    intervals.sort()
    for left, right in zip(intervals, intervals[1:]):
        if right[0] < left[1]:
            raise ValueError("overlapping_sessions")
    return {"venue": venue.strip().upper(), "timezone": tz_name,
            "calendar_date": day.isoformat(), "is_holiday": holiday,
            "sessions": normalized, "source": source.strip()}


class ExchangeCalendarStore:
    def __init__(self, database_path: str | None = None):
        self.database_path = database_path or os.getenv(
            "OSIRIS_CALENDAR_DB_PATH", "data/osiris_exchange_calendar.sqlite3")
        self._memory_anchor = None
        self._connection_path = self.database_path
        if self.database_path == ":memory:":
            # Each operation owns its connection; retain an isolated shared-memory
            # database for the lifetime of this store instead of losing its schema.
            self._connection_path = f"file:osiris-calendar-{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._memory_anchor = sqlite3.connect(self._connection_path, uri=True)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS calendar_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                venue TEXT NOT NULL, calendar_date TEXT NOT NULL,
                effective_at TEXT NOT NULL, recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                timezone TEXT NOT NULL, is_holiday INTEGER NOT NULL,
                sessions_json TEXT NOT NULL, source TEXT NOT NULL,
                UNIQUE(venue, calendar_date, effective_at))""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_calendar_asof
                ON calendar_versions(venue, calendar_date, effective_at DESC)""")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self._connection_path, timeout=10,
                             uri=self.database_path == ":memory:")
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def put(self, record: dict[str, Any], effective_at: str | None = None) -> dict[str, Any]:
        item = normalize_record(record)
        effective = aware_utc(effective_at).isoformat()
        with self._connect() as db:
            try:
                db.execute("""INSERT INTO calendar_versions
                    (venue, calendar_date, effective_at, timezone, is_holiday, sessions_json, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (item["venue"], item["calendar_date"], effective, item["timezone"],
                     int(item["is_holiday"]), json.dumps(item["sessions"], separators=(",", ":")),
                     item["source"]))
            except sqlite3.IntegrityError as exc:
                raise ValueError("calendar_version_already_exists") from exc
        return {**item, "effective_at": effective, "stored": True}

    def get(self, venue: str, day: str, as_of: str | None = None) -> dict[str, Any] | None:
        try:
            parsed_day = date.fromisoformat(day).isoformat()
        except (TypeError, ValueError) as exc:
            raise ValueError("calendar_date_must_be_yyyy_mm_dd") from exc
        cutoff = aware_utc(as_of).isoformat()
        with self._connect() as db:
            row = db.execute("""SELECT * FROM calendar_versions
                WHERE venue=? AND calendar_date=? AND effective_at<=?
                ORDER BY effective_at DESC, id DESC LIMIT 1""",
                (venue.strip().upper(), parsed_day, cutoff)).fetchone()
        if not row:
            return None
        return {"venue": row["venue"], "calendar_date": row["calendar_date"],
                "effective_at": row["effective_at"], "recorded_at": row["recorded_at"],
                "timezone": row["timezone"], "is_holiday": bool(row["is_holiday"]),
                "sessions": json.loads(row["sessions_json"]), "source": row["source"]}

    def status(self, venue: str, at: str, as_of: str | None = None) -> dict[str, Any]:
        instant = aware_utc(at)
        venue = venue.strip().upper()
        cutoff = aware_utc(as_of).isoformat()
        calendar_available = False
        candidates = []
        # A venue can be up to fourteen hours ahead of UTC or twelve behind.
        # Its previous local session date can therefore be two UTC dates back.
        # Resolve each version's timezone before deciding which dates apply.
        for offset in (-2, -1, 0, 1):
            probe = (instant + timedelta(days=offset)).date().isoformat()
            item = self.get(venue, probe, cutoff)
            if item:
                zone = ZoneInfo(item["timezone"])
                local_day = date.fromisoformat(item["calendar_date"])
                current_day = instant.astimezone(zone).date()
                if local_day == current_day:
                    calendar_available = True
                elif local_day != current_day - timedelta(days=1):
                    continue
                for session in item["sessions"]:
                    open_time = time.fromisoformat(session["open"])
                    start = _local_datetime(local_day, open_time, zone)
                    close_time = time.fromisoformat(session["close"])
                    end_day = local_day + timedelta(days=1) if close_time <= open_time else local_day
                    end = _local_datetime(end_day, close_time, zone)
                    start_utc, end_utc = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
                    candidates.append((start_utc, end_utc, item))
        for start, end, item in candidates:
            if start <= instant < end:
                return {"venue": venue.upper(), "at": instant.isoformat(), "is_open": True,
                        "session_open": start.isoformat(), "session_close": end.isoformat(),
                        "timezone": item["timezone"], "calendar_source": item["source"],
                        "calendar_data_available": True}
        return {"venue": venue.upper(), "at": instant.isoformat(), "is_open": False,
                "calendar_data_available": calendar_available,
                "reason": "outside_configured_sessions" if calendar_available else "calendar_data_unavailable"}
