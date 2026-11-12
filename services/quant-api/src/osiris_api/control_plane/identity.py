"""RFC 6238 TOTP enrollment, replay rejection, rate limiting and opaque sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import secrets
import struct
import time

from .database import canonical, identifier
from .secrets import EncryptedSecrets


def totp(seed: str, counter: int, *, digits: int = 6) -> str:
    key = base64.b32decode(seed + "=" * (-len(seed) % 8), casefold=True)
    raw = hmac.digest(key, struct.pack(">Q", counter), "sha1")
    offset = raw[-1] & 15
    value = struct.unpack(">I", raw[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


class IdentityService:
    def __init__(self, secrets_store: EncryptedSecrets, *, clock=time.time):
        self.secrets, self.db, self.clock = secrets_store, secrets_store.db, clock

    def _now(self, conn) -> float:
        # Sample under the write lock, and retain the high-water mark across
        # restarts so a wall-clock rollback cannot revive expired credentials.
        now = self.clock()
        if (type(now) not in (int, float) or not 0 <= now <= 253402300799
                or not math.isfinite(now)):
            raise ValueError("invalid_identity_clock")
        row = conn.execute(
            "SELECT body FROM cp_documents WHERE collection='authority-clock' AND id='identity'"
        ).fetchone()
        if row and now < json.loads(row[0])["observed_at"]:
            raise ValueError("identity_clock_rollback")
        conn.execute(
            "INSERT INTO cp_documents VALUES ('authority-clock','identity',1,?) "
            "ON CONFLICT(collection,id) DO UPDATE SET version=version+1,body=excluded.body",
            (canonical({"observed_at": now}).decode(),),
        )
        return now

    def enroll(self, actor: str) -> str:
        actor = identifier(actor)
        seed = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
        sealed = self.secrets.seal("totp:" + actor, seed)
        with self.db.transaction() as conn:
            now = self._now(conn)
            row = conn.execute(
                "SELECT body FROM cp_documents WHERE collection='mfa' AND id=?", (actor,)
            ).fetchone()
            if row and json.loads(row[0]).get("confirmed"):
                raise ValueError("mfa_already_enrolled")
            state = {
                "confirmed": False,
                "enroll_until": now + 600,
                "counter": -1,
                "failures": 0,
                "locked_until": 0,
            }
            for collection, key, value in (
                ("secrets", "totp:" + actor, sealed),
                ("mfa", actor, state),
            ):
                conn.execute(
                    "INSERT INTO cp_documents VALUES (?,?,1,?) ON CONFLICT(collection,id) "
                    "DO UPDATE SET version=version+1,body=excluded.body",
                    (collection, key, canonical(value).decode()),
                )
        return seed

    def verify(self, actor: str, code: str) -> float:
        actor = identifier(actor)
        seed = self.secrets.get("totp:" + actor)
        if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
            code = "invalid"
        rejected = False
        with self.db.transaction() as conn:
            now = self._now(conn)
            row = conn.execute(
                "SELECT version,body FROM cp_documents WHERE collection='mfa' AND id=?", (actor,)
            ).fetchone()
            if not row:
                raise ValueError("mfa_not_enrolled")
            state = json.loads(row["body"])
            if state["locked_until"] > now or (
                not state["confirmed"] and state["enroll_until"] < now
            ):
                raise ValueError("mfa_unavailable")
            current = int(now // 30)
            matched = next(
                (
                    counter
                    for counter in (current - 1, current, current + 1)
                    if counter >= 0
                    and counter > state["counter"]
                    and hmac.compare_digest(totp(seed, counter), code)
                ),
                None,
            )
            if matched is None:
                state["failures"] += 1
                if state["failures"] >= 5:
                    state.update(locked_until=now + 300, failures=0)
                rejected = True
            else:
                state.update(counter=matched, confirmed=True, failures=0, locked_until=0)
            conn.execute(
                "UPDATE cp_documents SET version=version+1,body=? WHERE collection='mfa' AND id=?",
                (canonical(state).decode(), actor),
            )
        if rejected:
            raise ValueError("invalid_or_replayed_totp")
        return now

    def login(self, actor: str, role: str, code: str, config_digest: str) -> str:
        now = self.verify(actor, code)
        token = secrets.token_urlsafe(32)
        key = hashlib.sha256(token.encode()).hexdigest()
        self.db.put(
            "sessions",
            key,
            {
                "actor": actor,
                "role": role,
                "expires": now + 900,
                "config_digest": config_digest,
                "revoked": False,
            },
        )
        return token

    def session(self, token: str, config_digest: str) -> dict:
        if not isinstance(token, str) or not 32 <= len(token) <= 128:
            raise ValueError("invalid_session")
        with self.db.transaction() as conn:
            now = self._now(conn)
            row = conn.execute(
                "SELECT body FROM cp_documents WHERE collection='sessions' AND id=?",
                (hashlib.sha256(token.encode()).hexdigest(),),
            ).fetchone()
            item = json.loads(row[0]) if row else None
        # Commit the observed clock even for an expired/revoked session.
        if (
            not item
            or item["expires"] <= now
            or item["revoked"]
            or not hmac.compare_digest(item["config_digest"], config_digest)
        ):
            raise ValueError("invalid_session")
        return item

    def logout(self, token: str, config_digest: str) -> None:
        self.session(token, config_digest)
        key = hashlib.sha256(token.encode()).hexdigest()
        item = self.db.get("sessions", key)
        self.db.put(
            "sessions", key, {**item["value"], "revoked": True}, expected_version=item["version"]
        )
