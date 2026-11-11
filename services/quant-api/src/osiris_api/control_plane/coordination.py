"""Single-authority leases, fenced writes and leased HTTP-worker jobs.

SQLite is the central authority, not a distributed-consensus implementation.
All worker decisions use the authority's clock; clients cannot supply a time.
"""

from __future__ import annotations

import json
import secrets
import time

from .database import Database, canonical, identifier


class Coordinator:
    def __init__(self, db: Database, *, clock=time.time):
        self.db, self.clock = db, clock

    def acquire(self, name: str, owner: str, *, ttl: int = 30) -> dict:
        name, owner = identifier(name), identifier(owner)
        if type(ttl) is not int or not 1 <= ttl <= 300:
            raise ValueError("invalid_lease_ttl")
        now = self.clock()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM cp_leases WHERE name=?", (name,)).fetchone()
            if row and row["expires"] > now:
                raise ValueError("lease_held")
            epoch = 1 if row is None else row["epoch"] + 1
            conn.execute(
                "INSERT INTO cp_leases VALUES (?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                "owner=excluded.owner,epoch=excluded.epoch,expires=excluded.expires",
                (name, owner, epoch, now + ttl),
            )
        return {"name": name, "owner": owner, "epoch": epoch, "expires": now + ttl}

    def _assert_lease(self, conn, name, owner, epoch):
        row = conn.execute("SELECT * FROM cp_leases WHERE name=?", (identifier(name),)).fetchone()
        if (
            not row
            or type(epoch) is not int
            or row["owner"] != owner
            or row["epoch"] != epoch
            or row["expires"] <= self.clock()
        ):
            raise ValueError("stale_or_foreign_lease")

    def renew(self, name: str, owner: str, epoch: int, *, ttl: int = 30):
        if type(ttl) is not int or not 1 <= ttl <= 300:
            raise ValueError("invalid_lease_ttl")
        with self.db.transaction() as conn:
            self._assert_lease(conn, name, owner, epoch)
            expires = self.clock() + ttl
            conn.execute("UPDATE cp_leases SET expires=? WHERE name=?", (expires, name))
        return {"name": name, "owner": owner, "epoch": epoch, "expires": expires}

    def write(self, name: str, owner: str, epoch: int, key: str, value: object):
        key, body = identifier(key), canonical(value).decode()
        with self.db.transaction() as conn:
            self._assert_lease(conn, name, owner, epoch)
            previous = conn.execute(
                "SELECT epoch FROM cp_writes WHERE resource=? AND id=?", (name, key)
            ).fetchone()
            if previous and previous[0] > epoch:
                raise ValueError("fencing_token_rejected")
            conn.execute(
                "INSERT INTO cp_writes VALUES (?,?,?,?) ON CONFLICT(resource,id) "
                "DO UPDATE SET epoch=excluded.epoch,body=excluded.body",
                (name, key, epoch, body),
            )
        return {"resource": name, "id": key, "epoch": epoch}

    def submit(self, job_id: str, kind: str, payload: object) -> dict:
        job_id, kind, body = identifier(job_id), identifier(kind), canonical(payload).decode()
        with self.db.transaction() as conn:
            existing = conn.execute("SELECT * FROM cp_jobs WHERE id=?", (job_id,)).fetchone()
            if existing:
                if existing["kind"] != kind or existing["body"] != body:
                    raise ValueError("job_id_conflict")
                return self._public_job(existing)
            conn.execute(
                "INSERT INTO cp_jobs(id,kind,body,status) VALUES (?,?,?,'queued')",
                (job_id, kind, body),
            )
        return {"id": job_id, "kind": kind, "status": "queued", "attempts": 0, "result": None}

    @staticmethod
    def _public_job(row):
        return {
            "id": row["id"],
            "kind": row["kind"],
            "status": row["status"],
            "attempts": row["attempts"],
            "result": json.loads(row["result"]) if row["result"] else None,
        }

    def jobs(self, *, limit: int = 100) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("invalid_limit")
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM cp_jobs ORDER BY rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._public_job(row) for row in rows]

    def claim(self, worker: str, kinds: list[str], *, ttl: int = 60) -> dict | None:
        worker = identifier(worker)
        if (
            not isinstance(kinds, list)
            or not 1 <= len(kinds) <= 20
            or type(ttl) is not int
            or not 1 <= ttl <= 300
        ):
            raise ValueError("invalid_claim")
        kinds = [identifier(kind) for kind in kinds]
        now, token = self.clock(), secrets.token_urlsafe(32)
        with self.db.transaction() as conn:
            # Abandoned work is retried, never silently treated as successful.
            conn.execute(
                "UPDATE cp_jobs SET status='queued',owner=NULL,token=NULL WHERE status='running' AND expires<=? AND attempts<3",
                (now,),
            )
            conn.execute(
                "UPDATE cp_jobs SET status='failed',result=? WHERE status='running' AND expires<=? AND attempts>=3",
                ('{"error":"worker_lease_exhausted"}', now),
            )
            placeholders = ",".join("?" for _ in kinds)
            row = conn.execute(
                f"SELECT * FROM cp_jobs WHERE status='queued' AND kind IN ({placeholders}) ORDER BY rowid LIMIT 1",
                kinds,
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE cp_jobs SET status='running',owner=?,token=?,expires=?,attempts=attempts+1 WHERE id=?",
                (worker, token, now + ttl, row["id"]),
            )
        return {
            "id": row["id"],
            "kind": row["kind"],
            "payload": json.loads(row["body"]),
            "claim_token": token,
            "expires": now + ttl,
            "attempt": row["attempts"] + 1,
        }

    def finish(self, job_id: str, worker: str, token: str, result: object, *, failed: bool = False):
        body = canonical(result).decode()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM cp_jobs WHERE id=?", (identifier(job_id),)).fetchone()
            if (
                not row
                or row["status"] != "running"
                or row["owner"] != worker
                or row["token"] != token
                or row["expires"] <= self.clock()
            ):
                raise ValueError("stale_or_foreign_job_claim")
            conn.execute(
                "UPDATE cp_jobs SET status=?,result=?,token=NULL WHERE id=?",
                ("failed" if failed else "completed", body, job_id),
            )
        return {"id": job_id, "status": "failed" if failed else "completed"}
