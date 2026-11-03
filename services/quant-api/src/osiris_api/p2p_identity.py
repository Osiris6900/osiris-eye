"""Ed25519 node identity and proof-of-possession enrollment primitives."""
from __future__ import annotations
import base64, hashlib, os, secrets, sqlite3, time
from pathlib import Path
from typing import Any
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

PROTOCOL_VERSION = "osiris-eye:p2p:enroll:v1"
CHALLENGE_TTL_SECONDS = 300

def _enc(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

def _dec(value: str) -> bytes:
    if not value or len(value) > 512:
        raise ValueError("invalid_base64_value")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

def generate_node_identity() -> dict[str, str]:
    """Generate an Ed25519 identity locally; never upload the private key."""
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"node_id": hashlib.sha256(public).hexdigest(), "public_key": _enc(public),
            "private_key": _enc(private), "key_type": "ed25519"}

def _message(challenge_id: str, nonce: str, node_id: str) -> bytes:
    return f"{PROTOCOL_VERSION}:{challenge_id}:{nonce}:{node_id}".encode()

def sign_enrollment_challenge(private_key_b64: str, challenge_id: str, nonce: str, node_id: str) -> str:
    return _enc(Ed25519PrivateKey.from_private_bytes(_dec(private_key_b64)).sign(_message(challenge_id, nonce, node_id)))

def _verify(public_key_b64: str, signature_b64: str, challenge_id: str, nonce: str, node_id: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(_dec(public_key_b64)).verify(_dec(signature_b64), _message(challenge_id, nonce, node_id))
        return True
    except InvalidSignature:
        return False

class P2PIdentityRegistry:
    """Local SQLite registry; shared multi-node storage is a later milestone."""
    def __init__(self, database_path: str | None = None, trusted_public_keys: set[str] | None = None):
        self.database_path = database_path or os.getenv("OSIRIS_P2P_DB_PATH", "./data/osiris-p2p.sqlite3")
        if self.database_path != ":memory:":
            Path(self.database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self.trusted_public_keys = trusted_public_keys if trusted_public_keys is not None else {
            x.strip() for x in os.getenv("OSIRIS_P2P_TRUSTED_PUBLIC_KEYS", "").split(",") if x.strip()
        }
        self._initialize()

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def _initialize(self):
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS p2p_challenges (challenge_id TEXT PRIMARY KEY, nonce TEXT NOT NULL, created_at REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0)")
            db.execute("CREATE TABLE IF NOT EXISTS p2p_nodes (node_id TEXT PRIMARY KEY, public_key TEXT NOT NULL UNIQUE, trust_level TEXT NOT NULL, enrolled_at REAL NOT NULL, last_seen_at REAL NOT NULL)")

    def create_challenge(self) -> dict[str, Any]:
        cid, nonce, now = secrets.token_urlsafe(24), secrets.token_urlsafe(32), time.time()
        with self._connect() as db:
            db.execute("INSERT INTO p2p_challenges(challenge_id,nonce,created_at) VALUES(?,?,?)", (cid, nonce, now))
        return {"challenge_id": cid, "nonce": nonce, "expires_in_seconds": CHALLENGE_TTL_SECONDS, "protocol": PROTOCOL_VERSION}

    def enroll(self, public_key: str, signature: str, challenge_id: str) -> dict[str, Any]:
        public = _dec(public_key)
        if len(public) != 32:
            raise ValueError("invalid_ed25519_public_key")
        node_id = hashlib.sha256(public).hexdigest()
        with self._connect() as db:
            ch = db.execute("SELECT nonce,created_at,used FROM p2p_challenges WHERE challenge_id=?", (challenge_id,)).fetchone()
            if ch is None or ch["used"]:
                raise ValueError("challenge_missing_or_already_used")
            if time.time() - ch["created_at"] > CHALLENGE_TTL_SECONDS:
                raise ValueError("challenge_expired")
            if not _verify(public_key, signature, challenge_id, ch["nonce"], node_id):
                raise ValueError("invalid_node_signature")
            trust = "trusted" if public_key in self.trusted_public_keys else "public_untrusted"
            now = time.time()
            db.execute("UPDATE p2p_challenges SET used=1 WHERE challenge_id=?", (challenge_id,))
            db.execute("""INSERT INTO p2p_nodes(node_id,public_key,trust_level,enrolled_at,last_seen_at)
                VALUES(?,?,?,?,?) ON CONFLICT(node_id) DO UPDATE SET public_key=excluded.public_key,
                trust_level=excluded.trust_level,last_seen_at=excluded.last_seen_at""",
                (node_id, public_key, trust, now, now))
        return {"node_id": node_id, "trust_level": trust, "enrolled": True,
                "permissions": ["peer_discovery"] if trust == "public_untrusted" else ["peer_discovery", "approved_compute"]}

    def list_nodes(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT node_id,trust_level,enrolled_at,last_seen_at FROM p2p_nodes ORDER BY enrolled_at").fetchall()
        return [dict(row) for row in rows]

    def status(self) -> dict[str, Any]:
        nodes = self.list_nodes()
        return {"network_mode": "hybrid_p2p_foundation", "identity_protocol": PROTOCOL_VERSION,
            "node_count": len(nodes), "trusted_node_count": sum(n["trust_level"] == "trusted" for n in nodes),
            "public_untrusted_node_count": sum(n["trust_level"] == "public_untrusted" for n in nodes),
            "transport_enabled": False, "peer_discovery_enabled": False, "distributed_compute_enabled": False,
            "shared_durable_registry": False, "market_data_access": False, "live_execution": False,
            "note": "Identity enrollment only; no internet-wide P2P transport or trading permissions are enabled."}
