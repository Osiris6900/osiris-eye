"""Optional durable feed-revocation database.

If OSIRIS_FEED_REVOCATIONS_DB is set, SQLite is authoritative for suspensions
across process restart. Store read/write failure MUST block admission; never
fall back to the process-local set. No operator identity or credentials stored.

Without this setting, the legacy process-local revocation behavior remains for
synthetic developer fixtures. It is NOT durable production revocation.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3

ENV = "OSIRIS_FEED_REVOCATIONS_DB"


class RevocationStoreError(RuntimeError):
    """Unable to prove current revocation state; caller must fail closed."""


def enabled() -> bool:
    # Deliberately treat an explicitly configured blank path as an error.
    return ENV in os.environ


def _path() -> Path:
    configured = os.getenv(ENV, "")
    if not configured or configured == ":memory:":
        raise RevocationStoreError("revocation_path_invalid")
    path = Path(configured)
    if not path.is_absolute() or path.is_symlink():
        raise RevocationStoreError("revocation_path_invalid")
    return path


def _connect() -> sqlite3.Connection:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=5)
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("""CREATE TABLE IF NOT EXISTS feed_revocations (
                adapter_name TEXT PRIMARY KEY NOT NULL,
                suspended_at TEXT NOT NULL
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS feed_revocation_audit (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                adapter_name TEXT NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('suspend','restore')),
                recorded_at TEXT NOT NULL
            )""")
            conn.commit()
            return conn
        except (sqlite3.DatabaseError, sqlite3.OperationalError):
            conn.close()
            raise
    except (OSError, sqlite3.Error) as exc:
        raise RevocationStoreError("revocation_store_unavailable") from exc


def suspended() -> frozenset[str]:
    with closing(_connect()) as conn:
        try:
            return frozenset(
                row[0] for row in conn.execute(
                    "SELECT adapter_name FROM feed_revocations"
                ).fetchall()
            )
        except sqlite3.Error as exc:
            raise RevocationStoreError("revocation_store_unavailable") from exc


def set_suspended(adapter_name: str, active: bool) -> None:
    if not isinstance(adapter_name, str) or not adapter_name:
        raise RevocationStoreError("revocation_adapter_invalid")
    if active is not True and active is not False:
        raise RevocationStoreError("revocation_state_invalid")
    timestamp = datetime.now(timezone.utc).isoformat()
    with closing(_connect()) as conn:
        try:
            with conn:
                if active:
                    conn.execute(
                        """INSERT INTO feed_revocations(adapter_name,suspended_at)
                           VALUES (?,?) ON CONFLICT(adapter_name)
                           DO UPDATE SET suspended_at=excluded.suspended_at""",
                        (adapter_name, timestamp),
                    )
                else:
                    conn.execute(
                        "DELETE FROM feed_revocations WHERE adapter_name=?",
                        (adapter_name,),
                    )
                conn.execute(
                    """INSERT INTO feed_revocation_audit(
                        adapter_name,action,recorded_at) VALUES(?,?,?)""",
                    (adapter_name, "suspend" if active else "restore", timestamp),
                )
        except sqlite3.Error as exc:
            raise RevocationStoreError("revocation_store_unavailable") from exc
