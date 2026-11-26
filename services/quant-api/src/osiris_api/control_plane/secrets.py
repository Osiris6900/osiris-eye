"""Versioned local keys and an explicit HTTPS HashiCorp Vault KV-v2 client.

Operators load a key ring from ``env:NAME`` or ``file:PATH``. Each key has a
purpose, key id, and version. Decryption and signature verification accept
retired versions. New encryption and signing use only the active version.
Secret values are not logged, returned, or included in ``repr``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import stat
from collections.abc import Mapping
from urllib.parse import quote, urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .custody import sign_bundle, verify_bundle
from .database import Database, identifier

SCHEMA = "osiris.key-ring.v1"
_MAX_DOCUMENT = 1_048_576
_LOG = logging.getLogger("osiris_api.control_plane.secrets")
_FIELDS = {"purpose", "key_id", "version", "status", "algorithm", "encoding", "material"}
_PURPOSES = {
    "identity-master": "aes-256-gcm",
    "audit-signing": "ed25519",
    "api-token": "opaque",
    "broker-credential": "opaque",
}
_PLACEHOLDER_TEXT = {
    "secret", "password", "changeme", "placeholder", "sample", "example",
    "todo", "test", "none", "null", "dummy",
}
_PLACEHOLDER_TOKENS = (
    "changeme", "placeholder", "sample-key", "example-key", "replace_me",
    "replace-me", "your-key", "not-a-real", "test-secret", "test-key",
    "insert-key", "dummy-key", "password",
)


class KeyConfigurationError(ValueError):
    """Operator-facing configuration refusal. The message is a stable code."""

    def __init__(self, code: str):
        if not isinstance(code, str) or not code or any(char.isspace() for char in code):
            code = "invalid_key_configuration"
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"KeyConfigurationError({self.code!r})"


def _refuse(code: str) -> None:
    _LOG.error("key_configuration_refused code=%s", code)
    raise KeyConfigurationError(code)


class KeyVersion:
    """One key version. ``repr`` exposes the id and version, never the bytes."""

    __slots__ = ("algorithm", "key_id", "material", "purpose", "status", "version")

    def __init__(self, purpose: str, key_id: str, version: int, status: str,
                 algorithm: str, material: bytes):
        self.purpose = purpose
        self.key_id = key_id
        self.version = version
        self.status = status
        self.algorithm = algorithm
        self.material = material

    def retire(self) -> KeyVersion:
        return KeyVersion(
            self.purpose, self.key_id, self.version, "retired", self.algorithm, self.material
        )

    def export(self) -> dict:
        return {
            "purpose": self.purpose,
            "key_id": self.key_id,
            "version": self.version,
            "status": self.status,
            "algorithm": self.algorithm,
            "encoding": "base64",
            "material": base64.b64encode(self.material).decode(),
        }

    def public(self) -> dict:
        return {
            "purpose": self.purpose,
            "key_id": self.key_id,
            "version": self.version,
            "status": self.status,
            "algorithm": self.algorithm,
        }

    def __repr__(self) -> str:
        fields = ", ".join(f"{key}={value!r}" for key, value in self.public().items())
        return f"KeyVersion({fields})"

    __str__ = __repr__


class SecretRedactionFilter(logging.Filter):
    """Replace configured secret strings in log records. ``repr`` stays empty."""

    def __init__(self, values):
        super().__init__()
        texts: list[str] = []
        blobs: set[bytes] = set()
        for item in values:
            if isinstance(item, bytes):
                if len(item) >= 8:
                    blobs.add(item)
                encoded = base64.b64encode(item).decode()
                texts.append(encoded)
                try:
                    texts.append(item.decode("utf-8"))
                except UnicodeDecodeError:
                    pass
            elif isinstance(item, str):
                texts.append(item)
        self._texts = tuple(dict.fromkeys(text for text in texts if len(text) >= 8))
        self._blobs = blobs

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(self._redact(item) for item in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: self._redact(value) for key, value in record.args.items()}
        return True

    def _redact(self, value):
        if isinstance(value, bytes) and value in self._blobs:
            return b"[redacted]"
        if isinstance(value, str):
            for secret in self._texts:
                value = value.replace(secret, "[redacted]")
            return value
        return value

    def __repr__(self) -> str:
        return "SecretRedactionFilter(secrets='[redacted]')"

    __str__ = __repr__


def _attach_redaction(ring: KeyRing) -> None:
    logger = logging.getLogger("osiris_api.control_plane.secrets")
    for item in list(logger.filters):
        if isinstance(item, SecretRedactionFilter):
            logger.removeFilter(item)
    logger.addFilter(SecretRedactionFilter(ring.redaction_values()))


def _placeholder(material: bytes) -> bool:
    try:
        text = material.decode("utf-8").strip().lower()
    except UnicodeDecodeError:
        return False
    if not text:
        return False
    if text in _PLACEHOLDER_TEXT:
        return True
    return any(token in text for token in _PLACEHOLDER_TOKENS)


def _weak(material: bytes) -> bool:
    if len(set(material)) < 8:
        return True
    half = len(material) // 2
    return len(material) >= 32 and material[:half] == material[half:]


def _reject_material(material: bytes, algorithm: str) -> None:
    if not isinstance(material, bytes):
        _refuse("invalid_key_ring")
    if algorithm == "opaque":
        if len(material) < 16:
            _refuse("key_too_short")
        if len(material) > 16384:
            _refuse("invalid_key_ring")
    else:
        if len(material) < 32:
            _refuse("key_too_short")
        if len(material) != 32:
            _refuse("key_length_invalid")
    if _placeholder(material):
        _refuse("placeholder_key")
    if _weak(material):
        _refuse("weak_key")
    if algorithm == "ed25519":
        try:
            Ed25519PrivateKey.from_private_bytes(material)
        except Exception:
            _refuse("invalid_signing_key")
    elif algorithm == "aes-256-gcm":
        try:
            AESGCM(material)
        except Exception:
            _refuse("invalid_key_ring")
    elif algorithm != "opaque":
        _refuse("key_algorithm_mismatch")


def _validate_ring(versions: tuple[KeyVersion, ...]) -> None:
    if not versions or len(versions) > 32:
        _refuse("invalid_key_ring")
    if len({item.key_id for item in versions}) != len(versions):
        _refuse("duplicate_key_id")
    seen: list[tuple[KeyVersion, bytes]] = []
    groups: dict[str, list[KeyVersion]] = {}
    for item in versions:
        _reject_material(item.material, item.algorithm)
        digest = hashlib.sha256(item.material).digest()
        for previous, previous_digest in seen:
            if hmac.compare_digest(digest, previous_digest):
                if previous.purpose != item.purpose:
                    _refuse("key_reused_across_purposes")
                _refuse("key_version_material_reused")
        seen.append((item, digest))
        groups.setdefault(item.purpose, []).append(item)
    for group in groups.values():
        if len({item.version for item in group}) != len(group):
            _refuse("duplicate_key_version")
        active = [item for item in group if item.status == "active"]
        if len(active) != 1:
            _refuse("multiple_active_keys" if active else "missing_active_key")
        if any(item.version > active[0].version for item in group):
            _refuse("active_key_not_newest")


class KeyRing:
    """Immutable set of versioned keys. Construction validates and fails closed."""

    __slots__ = ("_versions",)

    def __init__(self, versions):
        self._versions = tuple(versions)
        _validate_ring(self._versions)
        _attach_redaction(self)

    def all(self) -> tuple[KeyVersion, ...]:
        return tuple(sorted(self._versions, key=lambda item: (item.purpose, item.version)))

    def versions(self, purpose: str) -> tuple[KeyVersion, ...]:
        return tuple(item for item in self.all() if item.purpose == purpose)

    def active(self, purpose: str) -> KeyVersion:
        matches = [item for item in self.versions(purpose) if item.status == "active"]
        if len(matches) != 1:
            _refuse("missing_active_key" if not matches else "multiple_active_keys")
        return matches[0]

    def public_rows(self) -> list[dict]:
        return [item.public() for item in self.all()]

    def redaction_values(self) -> tuple[bytes, ...]:
        return tuple(item.material for item in self._versions)

    def __repr__(self) -> str:
        summary = ", ".join(
            f"{row['purpose']}:{row['key_id']}:v{row['version']}:{row['status']}"
            for row in self.public_rows()
        )
        return f"KeyRing({summary})"

    __str__ = __repr__


def _parse_version(row: object) -> KeyVersion:
    if not isinstance(row, dict) or set(row) != _FIELDS:
        _refuse("invalid_key_ring")
    purpose = row["purpose"]
    if purpose not in _PURPOSES:
        _refuse("unknown_key_purpose")
    if row["algorithm"] != _PURPOSES[purpose] or row["encoding"] != "base64":
        _refuse("key_algorithm_mismatch")
    if row["status"] not in {"active", "retired"}:
        _refuse("invalid_key_ring")
    version = row["version"]
    if type(version) is not int or not 1 <= version <= 1_000_000:
        _refuse("key_version_required")
    try:
        key_id = identifier(row["key_id"])
    except ValueError:
        _refuse("invalid_key_id")
    material_text = row["material"]
    if not isinstance(material_text, str) or len(material_text) > 32768:
        _refuse("invalid_key_ring")
    try:
        material = base64.b64decode(material_text, validate=True)
    except Exception:
        _refuse("invalid_key_ring")
    return KeyVersion(purpose, key_id, version, row["status"], row["algorithm"], material)


def parse_key_ring(document: str) -> KeyRing:
    """Parse one key-ring document. The same document is used for env and file."""

    if not isinstance(document, str) or len(document) > _MAX_DOCUMENT:
        _refuse("invalid_key_ring")
    try:
        payload = json.loads(document)
    except json.JSONDecodeError:
        _refuse("invalid_key_ring")
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "keys"}
        or payload.get("schema") != SCHEMA
        or not isinstance(payload.get("keys"), list)
    ):
        _refuse("invalid_key_ring")
    return KeyRing([_parse_version(row) for row in payload["keys"]])


def _read_key_file(path: str) -> str:
    if not isinstance(path, str) or not path or "\x00" in path:
        _refuse("invalid_key_source")
    try:
        info = os.lstat(path)
    except OSError:
        _refuse("key_file_unreadable")
    if stat.S_ISLNK(info.st_mode):
        _refuse("key_file_symlink")
    if not stat.S_ISREG(info.st_mode):
        _refuse("key_file_not_regular")
    mode = stat.S_IMODE(info.st_mode)
    if mode & stat.S_IROTH:
        _refuse("key_file_world_readable")
    if mode & stat.S_IWOTH:
        _refuse("key_file_world_writable")
    if info.st_size > _MAX_DOCUMENT:
        _refuse("invalid_key_ring")
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        _refuse("key_file_unreadable")


def load_key_ring(source: str, *, environ: Mapping[str, str] | None = None) -> KeyRing:
    """Load ``env:NAME`` or ``file:PATH``. Both paths parse one key-ring document."""

    if not isinstance(source, str):
        _refuse("invalid_key_source")
    kind, separator, rest = source.partition(":")
    if separator != ":" or not rest or kind not in {"env", "file"}:
        _refuse("invalid_key_source")
    if kind == "file":
        return parse_key_ring(_read_key_file(rest))
    env = os.environ if environ is None else environ
    if not env.get(rest):
        _refuse("key_source_missing")
    return parse_key_ring(env[rest])


def _legacy_master_ring(env: Mapping[str, str]) -> KeyRing:
    key_id = env.get("OSIRIS_CONTROL_MASTER_KEY_ID", "")
    version_text = env.get("OSIRIS_CONTROL_MASTER_KEY_VERSION", "")
    if not key_id:
        _refuse("key_id_required")
    if not version_text.isdecimal() or not 1 <= len(version_text) <= 7:
        _refuse("key_version_required")
    try:
        material = base64.b64decode(env.get("OSIRIS_CONTROL_MASTER_KEY", ""), validate=True)
    except Exception:
        raise ValueError("32_byte_external_master_key_required") from None
    document = json.dumps({
        "schema": SCHEMA,
        "keys": [{
            "purpose": "identity-master",
            "key_id": key_id,
            "version": int(version_text),
            "status": "active",
            "algorithm": "aes-256-gcm",
            "encoding": "base64",
            "material": base64.b64encode(material).decode(),
        }],
    })
    return parse_key_ring(document)


def load_configured_key_ring(environ: Mapping[str, str] | None = None) -> KeyRing | None:
    """Return the configured ring, or None when no key source is set.

    A missing source is not an error. Paper-mode features that never call this
    keep running. Two sources at once, or a present but invalid source, refuse.
    """

    env = os.environ if environ is None else environ
    file_path = env.get("OSIRIS_KEY_RING_FILE", "")
    inline = env.get("OSIRIS_KEY_RING", "")
    legacy = env.get("OSIRIS_CONTROL_MASTER_KEY", "")
    if sum(bool(item) for item in (file_path, inline, legacy)) > 1:
        _refuse("ambiguous_key_source")
    if file_path:
        return load_key_ring("file:" + file_path, environ=env)
    if inline:
        return load_key_ring("env:OSIRIS_KEY_RING", environ=env)
    if legacy:
        return _legacy_master_ring(env)
    return None


def validate_startup_keys(environ: Mapping[str, str] | None = None) -> dict:
    """Validate configured keys. Absence returns ``configured: false`` and does not raise."""

    ring = load_configured_key_ring(environ)
    if ring is None:
        return {"configured": False, "keys": []}
    return {"configured": True, "keys": ring.public_rows()}


def _random_material(algorithm: str) -> bytes:
    for _ in range(8):
        if algorithm == "opaque":
            material = secrets.token_urlsafe(32).encode()
        else:
            material = os.urandom(32)
        try:
            _reject_material(material, algorithm)
        except KeyConfigurationError:
            continue
        return material
    _refuse("weak_key")


def generate_version(purpose: str, key_id: str, version: int, *, status: str = "active") -> KeyVersion:
    if purpose not in _PURPOSES:
        _refuse("unknown_key_purpose")
    try:
        checked_id = identifier(key_id)
    except ValueError:
        _refuse("invalid_key_id")
    if type(version) is not int or not 1 <= version <= 1_000_000:
        _refuse("key_version_required")
    if status not in {"active", "retired"}:
        _refuse("invalid_key_ring")
    algorithm = _PURPOSES[purpose]
    return KeyVersion(
        purpose, checked_id, version, status, algorithm, _random_material(algorithm)
    )


def add_purpose(ring: KeyRing | None, purpose: str, key_id: str) -> KeyRing:
    existing = () if ring is None else ring.all()
    if any(item.purpose == purpose for item in existing):
        _refuse("purpose_already_configured")
    return KeyRing((*existing, generate_version(purpose, key_id, 1)))


def rotate_purpose(ring: KeyRing, purpose: str, key_id: str) -> KeyRing:
    current = ring.versions(purpose)
    if not current:
        _refuse("purpose_not_configured")
    if any(item.key_id == key_id for item in ring.all()):
        _refuse("duplicate_key_id")
    nxt = max(item.version for item in current) + 1
    if nxt > 1_000_000:
        _refuse("key_version_required")
    kept = [
        item.retire() if item.purpose == purpose and item.status == "active" else item
        for item in ring.all()
    ]
    kept.append(generate_version(purpose, key_id, nxt))
    return KeyRing(tuple(kept))


def write_key_ring(path: str, ring: KeyRing) -> None:
    """Write a new ring as a private regular file. The bytes are not logged."""

    if not isinstance(path, str) or not path or "\x00" in path:
        _refuse("invalid_key_source")
    if os.path.lexists(path):
        _refuse("key_file_exists")
    raw = json.dumps(
        {"schema": SCHEMA, "keys": [item.export() for item in ring.all()]}, indent=2
    ).encode() + b"\n"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError:
        _refuse("key_file_unreadable")
    try:
        os.write(descriptor, raw)
    except OSError:
        os.close(descriptor)
        os.unlink(path)
        _refuse("key_file_unreadable")
    else:
        os.close(descriptor)
    try:
        os.chmod(path, 0o600)
        info = os.lstat(path)
    except OSError:
        os.unlink(path)
        _refuse("key_file_unreadable")
    if stat.S_ISLNK(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
        os.unlink(path)
        _refuse("key_file_world_readable")


def active_secret(ring: KeyRing, purpose: str) -> str:
    """Return the active opaque secret. Retired versions are not returned here."""

    version = ring.active(purpose)
    if version.algorithm != "opaque":
        _refuse("opaque_secret_required")
    try:
        return version.material.decode("utf-8")
    except UnicodeDecodeError:
        _refuse("secret_not_text")


def _same_secret(left: str, right: str) -> bool:
    try:
        return hmac.compare_digest(left.encode(), right.encode())
    except (TypeError, ValueError):
        return False


def recognize_secret(ring: KeyRing, purpose: str, presented: str) -> bool:
    """True when ``presented`` matches the active opaque secret or a retired one."""

    if not isinstance(presented, str):
        return False
    matched = False
    for version in ring.versions(purpose):
        if version.algorithm != "opaque":
            _refuse("opaque_secret_required")
        try:
            text = version.material.decode("utf-8")
        except UnicodeDecodeError:
            _refuse("secret_not_text")
        matched = matched or _same_secret(text, presented)
    return matched


def sign_evidence(ring: KeyRing, payload: object, *, purpose: str = "audit-signing") -> dict:
    """Sign with the active key only. The envelope names that key id and version."""

    version = ring.active(purpose)
    if version.algorithm != "ed25519":
        _refuse("signing_algorithm_required")
    private = Ed25519PrivateKey.from_private_bytes(version.material)
    return {
        "schema": "osiris.versioned-signature.v1",
        "purpose": purpose,
        "key_id": version.key_id,
        "key_version": version.version,
        "bundle": sign_bundle(payload, private),
    }


def verify_evidence_signature(ring: KeyRing, signed: dict, *, purpose: str = "audit-signing"):
    """Verify with the named version, including a retired key. Unknown keys fail."""

    if (
        not isinstance(signed, dict)
        or set(signed) != {"schema", "purpose", "key_id", "key_version", "bundle"}
        or signed.get("schema") != "osiris.versioned-signature.v1"
        or signed.get("purpose") != purpose
    ):
        raise ValueError("evidence_signature_invalid")
    matches = [item for item in ring.all() if item.key_id == signed.get("key_id")]
    if (
        len(matches) != 1
        or matches[0].purpose != purpose
        or matches[0].version != signed.get("key_version")
        or matches[0].algorithm != "ed25519"
    ):
        raise ValueError("evidence_signature_invalid")
    private = Ed25519PrivateKey.from_private_bytes(matches[0].material)
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return verify_bundle(signed["bundle"], public)


def open_identity_store(db: Database, environ: Mapping[str, str] | None = None) -> EncryptedSecrets:
    ring = load_configured_key_ring(environ)
    if ring is None:
        raise ValueError("32_byte_external_master_key_required")
    if not ring.versions("identity-master"):
        _refuse("identity_master_key_missing")
    return EncryptedSecrets(db, ring=ring)


def run_keys_action(
    action: str,
    *,
    environ: Mapping[str, str] | None = None,
    database: str | None = None,
    purpose: str | None = None,
    key_id: str | None = None,
    output: str | None = None,
    from_ring: str | None = None,
) -> dict:
    """Validate, provision, rotate, or re-encrypt. The result never contains key bytes."""

    if action == "validate":
        if from_ring:
            ring = load_key_ring("file:" + from_ring, environ=environ)
            return {"action": "validate", "configured": True, "keys": ring.public_rows()}
        described = validate_startup_keys(environ)
        return {"action": "validate", **described}
    if action in {"init", "rotate"}:
        if not purpose or not key_id or not output:
            _refuse("key_id_required")
        if action == "init":
            base = load_key_ring("file:" + from_ring) if from_ring else None
            ring = add_purpose(base, purpose, key_id)
        elif from_ring:
            ring = rotate_purpose(load_key_ring("file:" + from_ring), purpose, key_id)
        else:
            current = load_configured_key_ring(environ)
            if current is None:
                _refuse("key_ring_required")
            ring = rotate_purpose(current, purpose, key_id)
        write_key_ring(output, ring)
        active = ring.active(purpose)
        return {
            "action": action,
            "output": output,
            "purpose": purpose,
            "active_key_id": active.key_id,
            "active_version": active.version,
            "configured": True,
            "keys": ring.public_rows(),
        }
    if action == "reencrypt":
        if not database:
            _refuse("control_database_required")
        result = open_identity_store(Database(database), environ).reencrypt()
        result["action"] = "reencrypt"
        return result
    _refuse("invalid_key_configuration")


class ExternalKmsAdapter:
    """Disabled external KMS. No client is constructed and no network call is made."""

    enabled = False

    def __init__(self, *_args, **_kwargs):
        _refuse("external_kms_disabled")

    def unwrap(self, *_args, **_kwargs):
        _refuse("external_kms_disabled")


class EncryptedSecrets:
    def __init__(self, db: Database, key: bytes | None = None, *, ring: KeyRing | None = None):
        if ring is not None and key is not None:
            raise ValueError("32_byte_external_master_key_required")
        self.db = db
        if ring is None:
            if not isinstance(key, bytes) or len(key) != 32:
                raise ValueError("32_byte_external_master_key_required")
            self.cipher = AESGCM(key)
            self._active = None
            self._ciphers = None
            self._by_id = None
            self._purpose = None
            return
        try:
            active = ring.active("identity-master")
        except KeyConfigurationError as exc:
            if exc.code == "missing_active_key":
                _refuse("identity_master_key_missing")
            raise
        if active.algorithm != "aes-256-gcm":
            _refuse("key_algorithm_mismatch")
        self._purpose = "identity-master"
        self._active = active
        self._by_id = {item.key_id: item for item in ring.versions("identity-master")}
        self._ciphers = {item.key_id: AESGCM(item.material) for item in self._by_id.values()}
        self.cipher = self._ciphers[active.key_id]

    def __repr__(self) -> str:
        if self._active is None:
            return "EncryptedSecrets(key_id='unversioned', version=1)"
        return f"EncryptedSecrets(key_id={self._active.key_id!r}, version={self._active.version})"

    __str__ = __repr__

    def put(self, name: str, value: str, *, expected_version: int = 0) -> int:
        return self.db.put(
            "secrets", name, self.seal(name, value), expected_version=expected_version
        )

    def seal(self, name: str, value: str) -> dict:
        name = identifier(name)
        if not isinstance(value, str) or not 1 <= len(value.encode()) <= 16384:
            raise ValueError("invalid_secret_size")
        nonce = os.urandom(12)
        ciphertext = self.cipher.encrypt(nonce, value.encode(), name.encode())
        sealed = {"sealed": base64.b64encode(nonce + ciphertext).decode()}
        if self._active is not None:
            sealed["key_id"] = self._active.key_id
            sealed["version"] = self._active.version
        return sealed

    def get(self, name: str) -> str:
        name = identifier(name)
        item = self.db.get("secrets", name)
        if item is None:
            raise ValueError("secret_not_configured")
        return self.open(name, item["value"])

    def _candidates(self, sealed: dict):
        if self._ciphers is None:
            return [self.cipher]
        key_id = sealed.get("key_id")
        if key_id is None:
            return list(self._ciphers.values())
        version = self._by_id.get(key_id)
        if version is None or sealed.get("version") != version.version:
            raise ValueError("secret_decryption_failed")
        return [self._ciphers[key_id]]

    def open(self, name: str, sealed: dict) -> str:
        """Authenticate a stored value already read in the caller's transaction."""
        name = identifier(name)
        if not isinstance(sealed, dict) or not isinstance(sealed.get("sealed"), str):
            raise ValueError("secret_decryption_failed")
        try:
            raw = base64.b64decode(sealed["sealed"], validate=True)
            ciphers = self._candidates(sealed)
        except ValueError:
            raise ValueError("secret_decryption_failed") from None
        except Exception:
            raise ValueError("secret_decryption_failed") from None
        if len(raw) < 13:
            raise ValueError("secret_decryption_failed")
        opened = []
        for cipher in ciphers:
            try:
                opened.append(cipher.decrypt(raw[:12], raw[12:], name.encode()).decode())
            except Exception:
                continue
        if len(opened) != 1:
            raise ValueError("secret_decryption_failed")
        return opened[0]

    def reencrypt(self) -> dict:
        """Seal every stored secret again with the active key. Plaintext is not returned."""

        rows = self.db.list("secrets", limit=1000)
        if len(rows) >= 1000:
            raise ValueError("secret_rotation_incomplete")
        opened = [(row["id"], self.open(row["id"], row["value"]), row["version"]) for row in rows]
        for name, value, version in opened:
            self.put(name, value, expected_version=version)
        return {
            "reencrypted": len(opened),
            "active_key_id": None if self._active is None else self._active.key_id,
            "active_version": None if self._active is None else self._active.version,
            "purpose": self._purpose or "unversioned",
        }


class VaultSecrets:
    def __init__(self, address: str, token: str, transport, *, mount: str = "secret"):
        parsed = urlsplit(address)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError("https_vault_origin_required")
        if not token or any(c in token for c in "\r\n"):
            raise ValueError("vault_token_required")
        self.address, self.token, self.transport = address.rstrip("/"), token, transport
        self.mount = identifier(mount)

    def __repr__(self) -> str:
        return "VaultSecrets(address='[redacted]', token='[redacted]')"

    __str__ = __repr__

    def get(self, name: str) -> str:
        path = quote(identifier(name), safe="")
        try:
            result = self.transport.request(
                "GET",
                f"{self.address}/v1/{self.mount}/data/{path}",
                headers={"X-Vault-Token": self.token},
            )
            value = result["data"]["data"]["value"]
            if not isinstance(value, str) or not value or len(value.encode()) > 16384:
                raise ValueError()
            return value
        except Exception:
            raise ValueError("vault_read_failed") from None


def resolve_reference(reference: str, *, vault: VaultSecrets | None = None) -> str:
    if not isinstance(reference, str):
        raise ValueError("invalid_secret_reference")
    scheme, _, name = reference.partition(":")
    if scheme == "env" and name and name.replace("_", "A").isalnum():
        value = os.environ.get(name, "")
        if value:
            return value
    if scheme == "vault" and vault is not None:
        return vault.get(name)
    raise ValueError("secret_reference_unavailable")
