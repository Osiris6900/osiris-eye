"""Transactional control-plane storage. Each operation owns its connection."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def identifier(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise ValueError("invalid_identifier")
    return value


def canonical(value: object) -> bytes:
    pending, visited = [(value, 0)], 0
    while pending:
        node, depth = pending.pop()
        visited += 1
        if depth > 32 or visited > 200000:
            raise ValueError("json_structure_limit")
        if isinstance(node, dict):
            if len(node) + len(pending) > 200000 or any(not isinstance(key, str) for key in node):
                raise ValueError("json_string_keys_required")
            pending.extend((item, depth + 1) for item in node.values())
        elif isinstance(node, list):
            if len(node) + len(pending) > 200000:
                raise ValueError("json_structure_limit")
            pending.extend((item, depth + 1) for item in node)
        elif type(node) not in (str, int, float, bool, type(None)):
            raise ValueError("json_values_required")
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("invalid_json") from exc
    if len(raw) > 1_048_576:
        raise ValueError("payload_too_large")
    return raw


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


class Database:
    def __init__(self, path: str):
        original = Path(path)
        if original.is_symlink():
            raise ValueError("control_database_symlink_refused")
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        except FileExistsError:
            if not Path(self.path).is_file():
                raise ValueError("regular_control_database_required") from None
        with self.transaction() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS cp_documents (
                    collection TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL,
                    body TEXT NOT NULL, PRIMARY KEY(collection,id));
                CREATE TABLE IF NOT EXISTS cp_leases (
                    name TEXT PRIMARY KEY, owner TEXT NOT NULL, epoch INTEGER NOT NULL,
                    expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS cp_writes (
                    resource TEXT NOT NULL, id TEXT NOT NULL, epoch INTEGER NOT NULL,
                    body TEXT NOT NULL, PRIMARY KEY(resource,id));
                CREATE TABLE IF NOT EXISTS cp_jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, body TEXT NOT NULL,
                    status TEXT NOT NULL, owner TEXT, token TEXT, expires REAL,
                    attempts INTEGER NOT NULL DEFAULT 0, result TEXT);
                CREATE TABLE IF NOT EXISTS cp_events (
                    origin TEXT NOT NULL, seq INTEGER NOT NULL, id TEXT UNIQUE NOT NULL,
                    body TEXT NOT NULL, digest TEXT NOT NULL, signature TEXT NOT NULL,
                    PRIMARY KEY(origin,seq));
                CREATE TABLE IF NOT EXISTS cp_features (
                    feature TEXT NOT NULL, entity TEXT NOT NULL, event_ms INTEGER NOT NULL,
                    available_ms INTEGER NOT NULL, value REAL NOT NULL, source_hash TEXT NOT NULL,
                    PRIMARY KEY(feature,entity,event_ms,available_ms));
            """)

    @contextmanager
    def transaction(self):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get(self, collection: str, key: str) -> dict | None:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT version,body FROM cp_documents WHERE collection=? AND id=?",
                (identifier(collection), identifier(key)),
            ).fetchone()
        return (
            None if row is None else {"version": row["version"], "value": json.loads(row["body"])}
        )

    def put(self, collection: str, key: str, value: object, *, expected_version: int = 0) -> int:
        collection, key = identifier(collection), identifier(key)
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("invalid_version")
        body = canonical(value).decode()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT version FROM cp_documents WHERE collection=? AND id=?", (collection, key)
            ).fetchone()
            current = 0 if row is None else row[0]
            if current != expected_version:
                raise ValueError("version_conflict")
            conn.execute(
                "INSERT INTO cp_documents VALUES (?,?,?,?) ON CONFLICT(collection,id) "
                "DO UPDATE SET version=excluded.version,body=excluded.body",
                (collection, key, current + 1, body),
            )
        return current + 1

    def list(self, collection: str, *, limit: int = 100) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("invalid_limit")
        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT id,version,body FROM cp_documents WHERE collection=? ORDER BY id LIMIT ?",
                (identifier(collection), limit),
            ).fetchall()
        return [
            {"id": r["id"], "version": r["version"], "value": json.loads(r["body"])} for r in rows
        ]
