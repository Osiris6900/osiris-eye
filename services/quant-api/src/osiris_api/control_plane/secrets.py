"""Encrypted local credentials and an explicit HTTPS HashiCorp Vault KV-v2 client."""

from __future__ import annotations

import base64
import os
from urllib.parse import quote, urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .database import Database, identifier


class EncryptedSecrets:
    def __init__(self, db: Database, key: bytes):
        if not isinstance(key, bytes) or len(key) != 32:
            raise ValueError("32_byte_external_master_key_required")
        self.db, self.cipher = db, AESGCM(key)

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
        return {"sealed": base64.b64encode(nonce + ciphertext).decode()}

    def get(self, name: str) -> str:
        name = identifier(name)
        item = self.db.get("secrets", name)
        if item is None:
            raise ValueError("secret_not_configured")
        return self.open(name, item["value"])

    def open(self, name: str, sealed: dict) -> str:
        """Authenticate a stored value already read in the caller's transaction."""
        name = identifier(name)
        try:
            raw = base64.b64decode(sealed["sealed"], validate=True)
            return self.cipher.decrypt(raw[:12], raw[12:], name.encode()).decode()
        except Exception:
            raise ValueError("secret_decryption_failed") from None


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
