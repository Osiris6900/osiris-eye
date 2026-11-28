"""Single-writer fence for the canonical paper database.

SQLite serializes one file. This fence is for a second writer that must not
commit: another process or host presenting a different writer token. When
``OSIRIS_PAPER_WRITER_TOKEN`` is unset, every process shares ``shared-file``
and the file remains the authority. A copied database on two hosts is not
detected unless those hosts use different tokens.

The lease commits only with the mutation that holds it. A crash rolls the
renewal back with the order. This is not Raft, a quorum, or proof about a
copied file that reuses the same token.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_OWNER_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
SHARED_OWNER = "shared-file"
AUTHORITY_TTL = 30
_MAX_EPOCH = 2**63 - 1
_MAX_CLOCK = 253402300799

_writer_override: ContextVar[str | None] = ContextVar("paper_writer_owner", default=None)
_clock_override: ContextVar[Callable[[], float] | None] = ContextVar(
    "paper_writer_clock", default=None,
)


def _owner() -> str:
    override = _writer_override.get()
    if override is not None:
        owner = override
    else:
        configured = os.getenv("OSIRIS_PAPER_WRITER_TOKEN")
        owner = configured if configured else SHARED_OWNER
    if not isinstance(owner, str) or _OWNER_RE.fullmatch(owner) is None:
        raise ValueError("invalid_writer_token")
    return owner


def _now() -> float:
    clock = _clock_override.get()
    now = time.time() if clock is None else clock()
    if type(now) not in (int, float) or not math.isfinite(now) or not 0 <= now <= _MAX_CLOCK:
        raise ValueError("invalid_authority_clock")
    return float(now)


@contextmanager
def paper_writer(owner: str, *, clock: Callable[[], float] | None = None) -> Iterator[None]:
    """Test and operator hook for one fenced writer. Not an HTTP field."""
    if not isinstance(owner, str) or _OWNER_RE.fullmatch(owner) is None:
        raise ValueError("invalid_writer_token")
    owner_token = _writer_override.set(owner)
    clock_token = _clock_override.set(clock) if clock is not None else None
    try:
        yield
    finally:
        _writer_override.reset(owner_token)
        if clock_token is not None:
            _clock_override.reset(clock_token)


def ensure_authority_table(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_exec_authority (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        owner TEXT NOT NULL,
        epoch INTEGER NOT NULL,
        expires_at REAL NOT NULL,
        observed_at REAL NOT NULL)""")


def hold_paper_authority(conn: sqlite3.Connection) -> int:
    """Renew this writer's lease or take an expired one. Fail closed otherwise.

    Call inside the mutation's immediate transaction. A rollback drops the
    renewal. A foreign unexpired owner raises ``split_brain_rejected`` before
    any authority row changes.
    """
    ensure_authority_table(conn)
    now = _now()
    owner = _owner()
    row = conn.execute(
        "SELECT owner, epoch, expires_at, observed_at FROM paper_exec_authority WHERE id=1"
    ).fetchone()
    if row is not None and now < float(row[3]):
        raise ValueError("authority_clock_rollback")
    if row is not None and float(row[2]) > now and row[0] != owner:
        raise ValueError("split_brain_rejected")
    if row is None:
        epoch = 1
    elif row[0] == owner and float(row[2]) > now:
        epoch = int(row[1])
    else:
        epoch = int(row[1]) + 1
        if epoch > _MAX_EPOCH:
            raise ValueError("authority_epoch_exhausted")
    conn.execute(
        """INSERT INTO paper_exec_authority(id, owner, epoch, expires_at, observed_at)
           VALUES (1, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             owner=excluded.owner, epoch=excluded.epoch,
             expires_at=excluded.expires_at, observed_at=excluded.observed_at""",
        (owner, epoch, now + AUTHORITY_TTL, now),
    )
    return epoch


def authority_view(conn: sqlite3.Connection) -> dict[str, Any]:
    """Durable lease only. Reading does not renew it or require the writer."""
    present = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_exec_authority'"
    ).fetchone()
    blank = {"established": False, "live_execution": False}
    if present is None:
        return blank
    row = conn.execute(
        "SELECT owner, epoch, expires_at, observed_at FROM paper_exec_authority WHERE id=1"
    ).fetchone()
    if row is None:
        return blank
    return {
        "established": True,
        "epoch": int(row[1]),
        "expires_at": float(row[2]),
        "observed_at": float(row[3]),
        "owner_fingerprint": hashlib.sha256(str(row[0]).encode()).hexdigest()[:16],
        "live_execution": False,
    }
