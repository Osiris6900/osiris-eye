"""Authenticated end-to-end encrypted P2P message envelopes.

This is a cryptographic message layer, not a network transport. It uses an
ephemeral X25519 sender key, HKDF-SHA256, ChaCha20-Poly1305, and an Ed25519
signature from the sender's already-authenticated node identity.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path
from collections import OrderedDict
from typing import Any

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ENVELOPE_VERSION = "osiris-eye:p2p:e2ee:v1"
MAX_PLAINTEXT_BYTES = 1_048_576
MAX_CLOCK_SKEW_SECONDS = 300


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(value: str, *, max_length: int = 2_000_000) -> bytes:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ValueError("invalid_encoded_field")
    try:
        return base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except Exception as exc:
        raise ValueError("invalid_encoded_field") from exc


def _raw_private(key: Any) -> bytes:
    return key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )


def _raw_public(key: Any) -> bytes:
    return key.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def generate_transport_identity() -> dict[str, str]:
    """Generate an X25519 recipient keypair locally; never transmit its secret."""
    private = X25519PrivateKey.generate()
    public = private.public_key()
    return {
        "key_type": "x25519",
        "private_key": _b64e(_raw_private(private)),
        "public_key": _b64e(_raw_public(public)),
    }


def _derive_key(shared_secret: bytes, salt: bytes, sender_id: str, recipient_id: str) -> bytes:
    info = f"{ENVELOPE_VERSION}:{sender_id}:{recipient_id}".encode("ascii")
    return HKDF(algorithm=SHA256(), length=32, salt=salt, info=info).derive(shared_secret)


def encrypt_for_peer(
    plaintext: bytes,
    *,
    sender_node_id: str,
    sender_signing_private_key: str,
    sender_signing_public_key: str,
    recipient_node_id: str,
    recipient_transport_public_key: str,
) -> dict[str, Any]:
    """Encrypt bytes for one recipient and sign the complete envelope."""
    if not isinstance(plaintext, bytes) or len(plaintext) > MAX_PLAINTEXT_BYTES:
        raise ValueError("plaintext_must_be_bytes_under_1MiB")
    signing_public = _b64d(sender_signing_public_key, max_length=128)
    signing_private = Ed25519PrivateKey.from_private_bytes(
        _b64d(sender_signing_private_key, max_length=128)
    )
    if (
        len(signing_public) != 32
        or hashlib.sha256(signing_public).hexdigest() != sender_node_id
        or _raw_public(signing_private.public_key()) != signing_public
    ):
        raise ValueError("sender_node_identity_mismatch")

    recipient_raw = _b64d(recipient_transport_public_key, max_length=128)
    if len(recipient_raw) != 32:
        raise ValueError("invalid_recipient_transport_key")
    recipient_public = X25519PublicKey.from_public_bytes(recipient_raw)

    ephemeral_private = X25519PrivateKey.generate()
    salt, nonce = secrets.token_bytes(32), secrets.token_bytes(12)
    shared = ephemeral_private.exchange(recipient_public)
    key = _derive_key(shared, salt, sender_node_id, recipient_node_id)
    header = {
        "version": ENVELOPE_VERSION,
        "sender_node_id": sender_node_id,
        "recipient_node_id": recipient_node_id,
        "sender_signing_public_key": sender_signing_public_key,
        "ephemeral_public_key": _b64e(_raw_public(ephemeral_private.public_key())),
        "salt": _b64e(salt),
        "nonce": _b64e(nonce),
        "message_id": _b64e(secrets.token_bytes(16)),
        "issued_at": int(time.time()),
        "cipher": "ChaCha20-Poly1305",
        "kdf": "HKDF-SHA256",
    }
    ciphertext = ChaCha20Poly1305(key).encrypt(nonce, plaintext, _canonical(header))
    signed = {**header, "ciphertext": _b64e(ciphertext)}
    signature = signing_private.sign(_canonical(signed))
    return {**signed, "signature": _b64e(signature)}


class ReplayGuard:
    """Bounded process-local replay cache; use shared durable storage in production."""

    def __init__(self, capacity: int = 100_000):
        if capacity < 1:
            raise ValueError("capacity_must_be_positive")
        self.capacity = capacity
        self._seen: OrderedDict[str, None] = OrderedDict()

    def check_and_record(self, message_id: str) -> bool:
        if message_id in self._seen:
            return False
        self._seen[message_id] = None
        if len(self._seen) > self.capacity:
            self._seen.popitem(last=False)
        return True


class SQLiteReplayGuard:
    """Durable, atomic replay protection for a single node's local SQLite database.

    Entries are intentionally not evicted: deleting an old ID can make a replay
    acceptable after a clock rollback. When capacity is exhausted, new messages
    fail closed until an operator provisions a larger store or performs a reviewed
    migration.
    """

    def __init__(self, database_path: str | Path, capacity: int = 100_000):
        if capacity < 1:
            raise ValueError("capacity_must_be_positive")
        self.database_path = str(database_path)
        if self.database_path == ":memory:":
            raise ValueError("durable_replay_store_requires_file_path")
        self.capacity = capacity
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS replay_messages (
                    message_id TEXT PRIMARY KEY,
                    first_seen INTEGER NOT NULL
                )"""
            )
        finally:
            connection.close()
        try:
            Path(self.database_path).chmod(0o600)
        except OSError:
            # Permission hardening is best-effort on platforms without POSIX modes.
            pass

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path, timeout=30, isolation_level=None
        )
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def check_and_record(self, message_id: str) -> bool:
        if not isinstance(message_id, str) or not message_id or len(message_id) > 128:
            raise ValueError("invalid_message_id")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            inserted = connection.execute(
                "INSERT OR IGNORE INTO replay_messages(message_id, first_seen) "
                "VALUES (?, ?)",
                (message_id, int(time.time())),
            )
            if inserted.rowcount == 0:
                connection.execute("COMMIT")
                return False
            count = connection.execute(
                "SELECT COUNT(*) FROM replay_messages"
            ).fetchone()[0]
            if count > self.capacity:
                connection.execute("ROLLBACK")
                raise RuntimeError("replay_store_capacity_exceeded")
            connection.execute("COMMIT")
            return True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()


def decrypt_from_peer(
    envelope: dict[str, Any],
    *,
    recipient_node_id: str,
    recipient_transport_private_key: str,
    expected_sender_node_id: str,
    trusted_sender_signing_public_key: str,
    replay_guard: ReplayGuard | SQLiteReplayGuard,
    now: int | None = None,
) -> bytes:
    """Verify a pinned sender identity, reject stale/replayed envelopes, then decrypt."""
    if not isinstance(envelope, dict) or len(envelope) > 16:
        raise ValueError("invalid_envelope")
    required = {
        "version", "sender_node_id", "recipient_node_id",
        "sender_signing_public_key", "ephemeral_public_key", "salt", "nonce",
        "message_id", "issued_at", "cipher", "kdf", "ciphertext", "signature",
    }
    if set(envelope) != required:
        raise ValueError("invalid_envelope_fields")
    if envelope["version"] != ENVELOPE_VERSION or envelope["cipher"] != "ChaCha20-Poly1305" or envelope["kdf"] != "HKDF-SHA256":
        raise ValueError("unsupported_envelope_algorithm")
    if envelope["sender_node_id"] != expected_sender_node_id or envelope["recipient_node_id"] != recipient_node_id:
        raise ValueError("peer_identity_mismatch")
    trusted_raw = _b64d(trusted_sender_signing_public_key, max_length=128)
    if len(trusted_raw) != 32 or envelope["sender_signing_public_key"] != trusted_sender_signing_public_key:
        raise ValueError("untrusted_sender_key")
    if hashlib.sha256(trusted_raw).hexdigest() != expected_sender_node_id:
        raise ValueError("sender_node_identity_mismatch")

    issued_at = envelope["issued_at"]
    if not isinstance(issued_at, int):
        raise ValueError("invalid_message_timestamp")
    current = int(time.time()) if now is None else now
    if abs(current - issued_at) > MAX_CLOCK_SKEW_SECONDS:
        raise ValueError("message_expired_or_clock_skew")
    message_id = envelope["message_id"]
    message_id_raw = _b64d(message_id, max_length=64)
    if len(message_id_raw) != 16:
        raise ValueError("invalid_message_id")

    header = {key: envelope[key] for key in required if key not in {"ciphertext", "signature"}}
    signed = {**header, "ciphertext": envelope["ciphertext"]}
    try:
        Ed25519PublicKey.from_public_bytes(trusted_raw).verify(
            _b64d(envelope["signature"], max_length=128), _canonical(signed)
        )
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("sender_signature_invalid") from exc

    ephemeral_raw = _b64d(envelope["ephemeral_public_key"], max_length=128)
    salt = _b64d(envelope["salt"], max_length=128)
    nonce = _b64d(envelope["nonce"], max_length=64)
    if len(ephemeral_raw) != 32 or len(salt) != 32 or len(nonce) != 12:
        raise ValueError("invalid_key_or_nonce_length")
    recipient_private_raw = _b64d(recipient_transport_private_key, max_length=128)
    if len(recipient_private_raw) != 32:
        raise ValueError("invalid_recipient_transport_key")
    if not replay_guard.check_and_record(message_id):
        raise ValueError("replayed_message")

    shared = X25519PrivateKey.from_private_bytes(recipient_private_raw).exchange(
        X25519PublicKey.from_public_bytes(ephemeral_raw)
    )
    key = _derive_key(shared, salt, expected_sender_node_id, recipient_node_id)
    try:
        plaintext = ChaCha20Poly1305(key).decrypt(
            nonce, _b64d(envelope["ciphertext"]), _canonical(header)
        )
    except (InvalidTag, ValueError) as exc:
        raise ValueError("message_authentication_failed") from exc
    if len(plaintext) > MAX_PLAINTEXT_BYTES:
        raise ValueError("plaintext_too_large")
    return plaintext
