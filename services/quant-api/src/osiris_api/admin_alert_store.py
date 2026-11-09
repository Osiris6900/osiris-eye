"""Local SQLite history for alerts the admin centre actually raises.

The file is created only when a valid alert is recorded. A missing file is an
empty history. An existing file that is not this store is left unchanged and
reported unavailable. Paths and credentials are not returned.
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

ALERT_DB_ENV = "OSIRIS_ADMIN_ALERT_DB"
# Used when the env var is unset. Tests point the env var at a temp file.
DEFAULT_ALERT_DB_PATH = "./data/osiris-admin-alerts.sqlite3"
ALERT_SCHEMA = "osiris.admin_alert.v1"

_CODE = re.compile(r"^[a-z0-9_-]{1,80}$")
_ALERT_ID = re.compile(r"^[a-z0-9_-]{1,80}:[a-z0-9_-]{1,80}$")
_ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:\+00:00|Z)$")
_SEVERITY = frozenset({"info", "warning", "critical"})
_ROLES = frozenset({"viewer", "analyst", "operator", "risk_officer", "security_admin"})
_REASONS = frozenset({"not_configured", "alert_store_unavailable"})
_COLUMNS = (
    "alert_id", "kind", "severity", "message", "status",
    "raised_at", "acknowledged_at", "acknowledged_by",
)


class AlertStoreError(Exception):
    """Fail closed. The message is a stable code and never a filesystem path."""


def resolve_alert_db_path(env: Mapping[str, str] | None = None) -> str | None:
    source = os.environ if env is None else env
    if ALERT_DB_ENV not in source:
        return DEFAULT_ALERT_DB_PATH
    value = source.get(ALERT_DB_ENV)
    if not isinstance(value, str) or not value.strip() or value.strip() == ":memory:":
        return None
    return value


def unavailable_alert_history(reason: str) -> dict[str, Any]:
    if reason not in _REASONS:
        reason = "alert_store_unavailable"
    return {
        "storage": "unavailable",
        "durable": False,
        "initialized": False,
        "reason": reason,
        "active_count": None,
        "acknowledged_count": None,
        "active": None,
        "acknowledged": None,
    }


def _empty_history(*, initialized: bool) -> dict[str, Any]:
    return {
        "storage": "sqlite",
        "durable": True,
        "initialized": initialized,
        "reason": None,
        "active_count": 0,
        "acknowledged_count": 0,
        "active": [],
        "acknowledged": [],
    }


def _stamp(now: datetime | None) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("admin_now_missing_timezone")
    return current.astimezone(timezone.utc).isoformat()


def _iso(value: Any) -> str | None:
    if isinstance(value, str) and _ISO_UTC.fullmatch(value):
        return value
    return None


def _project(row: sqlite3.Row) -> dict[str, Any] | None:
    alert_id = row["alert_id"]
    kind = row["kind"]
    severity = row["severity"]
    status = row["status"]
    message = row["message"]
    if not isinstance(alert_id, str) or _ALERT_ID.fullmatch(alert_id) is None:
        return None
    if (
        not isinstance(kind, str) or _CODE.fullmatch(kind) is None
        or not alert_id.startswith(f"{kind}:")
    ):
        return None
    if severity not in _SEVERITY or status not in {"active", "acknowledged"}:
        return None
    if not isinstance(message, str) or not message or len(message) > 300:
        return None
    if any(ord(char) < 32 for char in message):
        message = "unclassified"
    acknowledged_by = row["acknowledged_by"]
    if acknowledged_by is not None and acknowledged_by not in _ROLES:
        acknowledged_by = None
    return {
        "schema": ALERT_SCHEMA,
        "alert_id": alert_id,
        "kind": kind,
        "key": alert_id.slice(len(kind) + 1),
        "severity": severity,
        "message": message,
        "status": status,
        "raised_at": _iso(row["raised_at"]),
        "acknowledged_at": _iso(row["acknowledged_at"]),
        "acknowledged_by": acknowledged_by if status == "acknowledged" else None,
    }


def _history_from_rows(rows: list[sqlite3.Row], *, initialized: bool) -> dict[str, Any]:
    active: list[dict[str, Any]] = []
    acknowledged: list[dict[str, Any]] = []
    for row in rows:
        projected = _project(row)
        if projected is None:
            continue
        if projected["status"] == "active":
            active.append(projected)
        else:
            acknowledged.append(projected)
    return {
        "storage": "sqlite",
        "durable": True,
        "initialized": initialized,
        "reason": None,
        "active_count": len(active),
        "acknowledged_count": len(acknowledged),
        "active": active,
        "acknowledged": acknowledged,
    }


def _open_ro(path: str) -> sqlite3.Connection:
    resolved = Path(path).resolve(strict=True)
    uri = f"file:{quote(resolved.as_posix())}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _schema_ok(connection: sqlite3.Connection) -> bool:
    present = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='admin_alerts'"
    ).fetchone()
    if present is None:
        return False
    columns = {row[1] for row in connection.execute("PRAGMA table_info(admin_alerts)")}
    return set(_COLUMNS) <= columns


def _read_rows(path: str) -> list[sqlite3.Row]:
    connection = _open_ro(path)
    try:
        if connection.execute("PRAGMA query_only").fetchone()[0] != 1 or not _schema_ok(connection):
            raise AlertStoreError("alert_store_unavailable")
        return list(connection.execute(
            "SELECT alert_id, kind, severity, message, status, raised_at, "
            "acknowledged_at, acknowledged_by FROM admin_alerts "
            "ORDER BY raised_at, alert_id"
        ))
    except sqlite3.Error as exc:
        raise AlertStoreError("alert_store_unavailable") from exc
    finally:
        connection.close()


def _normalize_flags(flags: Any, now: datetime | None) -> list[dict[str, str]]:
    if not isinstance(flags, list):
        return []
    stamp = _stamp(now)
    normalized: list[dict[str, str]] = []
    for flag in flags:
        if not isinstance(flag, Mapping):
            continue
        kind = flag.get("kind")
        key = flag.get("key", "global")
        severity = flag.get("severity")
        message = flag.get("message")
        if not isinstance(kind, str) or _CODE.fullmatch(kind) is None:
            continue
        if not isinstance(key, str) or _CODE.fullmatch(key) is None:
            continue
        if severity not in _SEVERITY or not isinstance(message, str) or not message.strip():
            continue
        normalized.append({
            "alert_id": f"{kind}:{key}",
            "kind": kind,
            "severity": severity,
            "message": message.strip()[:300],
            "raised_at": stamp,
        })
    return normalized


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE admin_alerts (
            alert_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            severity TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL,
            raised_at TEXT NOT NULL,
            acknowledged_at TEXT,
            acknowledged_by TEXT
        )"""
    )


def record_raised_alerts(
    path: str, flags: Any, *, now: datetime | None = None,
) -> dict[str, Any]:
    """Insert alerts the caller already decided to raise. Does not invent rows."""
    if not isinstance(path, str) or not path.strip() or path.strip() == ":memory:":
        return unavailable_alert_history("not_configured")
    pending = _normalize_flags(flags, now)
    file = Path(path)
    existed = file.is_file()
    if not pending and not existed:
        return _empty_history(initialized=False)
    if not pending:
        return _history_from_rows(_read_rows(path), initialized=True)
    try:
        if existed:
            _read_rows(path)  # refuse to migrate a foreign database
        else:
            file.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=10)
    except (OSError, sqlite3.Error, AlertStoreError) as exc:
        raise AlertStoreError("alert_store_unavailable") from exc
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        if not existed:
            _create_schema(connection)
        elif not _schema_ok(connection):
            raise AlertStoreError("alert_store_unavailable")
        for item in pending:
            existing = connection.execute(
                "SELECT status FROM admin_alerts WHERE alert_id=?", (item["alert_id"],),
            ).fetchone()
            if existing is not None:
                continue
            connection.execute(
                """INSERT INTO admin_alerts (
                    alert_id, kind, severity, message, status, raised_at,
                    acknowledged_at, acknowledged_by
                ) VALUES (?, ?, ?, ?, 'active', ?, NULL, NULL)""",
                (
                    item["alert_id"], item["kind"], item["severity"],
                    item["message"], item["raised_at"],
                ),
            )
        connection.commit()
    except AlertStoreError:
        connection.rollback()
        raise
    except sqlite3.Error as exc:
        connection.rollback()
        raise AlertStoreError("alert_store_unavailable") from exc
    finally:
        connection.close()
    rows = _read_rows(path)
    return _history_from_rows(rows, initialized=True)


def acknowledge_stored_alert(
    path: str, alert_id: str, role: str, *, now: datetime | None = None,
) -> dict[str, Any] | None:
    """Acknowledge one active stored alert. Does not create the database."""
    if not isinstance(path, str) or not Path(path).is_file():
        return None
    if not isinstance(alert_id, str) or _ALERT_ID.fullmatch(alert_id) is None:
        return None
    if role not in _ROLES:
        return None
    _read_rows(path)
    stamp = _stamp(now)
    connection = sqlite3.connect(path, timeout=10)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """UPDATE admin_alerts
               SET status='acknowledged', acknowledged_at=?, acknowledged_by=?
               WHERE alert_id=? AND status='active'""",
            (stamp, role, alert_id),
        )
        connection.commit()
        if cursor.rowcount != 1:
            return None
        row = connection.execute(
            "SELECT alert_id, kind, severity, message, status, raised_at, "
            "acknowledged_at, acknowledged_by FROM admin_alerts WHERE alert_id=?",
            (alert_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise AlertStoreError("alert_store_unavailable") from exc
    finally:
        connection.close()
    if row is None:
        return None
    return _project(row)


def clear_stored_alerts(path: str) -> None:
    """Test isolation. Leaves a foreign database untouched."""
    if not isinstance(path, str) or not Path(path).is_file():
        return
    try:
        _read_rows(path)
    except AlertStoreError:
        return
    connection = sqlite3.connect(path, timeout=10)
    try:
        connection.execute("DELETE FROM admin_alerts")
        connection.commit()
    finally:
        connection.close()
