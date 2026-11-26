"""Tamper-evident audit evidence for the paper book and the phase 16 journal.

Records are hash-chained from server-stamped content. A checkpoint binds the
chain head, sequence, and time, and is signed with Ed25519 when a signing key
file is configured. The verifier fails closed: ``ok`` only when the chain,
the external anchor, and the signature agree. Missing or inconsistent evidence
is ``unverifiable`` or ``tampered``.

The signing key is read from ``OSIRIS_AUDIT_SIGNING_KEY_FILE``. That variable
is unset by default. No private key is created or embedded here. See
``docs/AUDIT_EVIDENCE_KEYS.md``.
"""
from __future__ import annotations

import base64
import binascii
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)

from .control_plane.custody import sign_bundle, verify_bundle
from .control_plane.database import canonical, digest

PAPER_CHAIN = "paper_exec_audit"
PHASE16_CHAIN = "phase16_audit"
ANCHOR_SCHEMA = "osiris.audit-anchor.v1"
BUNDLE_SIGNATURE_SCHEMA = "osiris.audit-bundle-signature.v1"
_ACTOR_RE = re.compile(r"[A-Za-z0-9_.:@/-]{1,200}")
_GENESIS = "GENESIS"
_PHASE16_GENESIS = "0" * 64
_SIGNATURE_FIELDS = ("signature_envelope", "signature_status", "signed", "key_error")


def server_actor() -> tuple[str, bool]:
    """Return the service identity and whether an operator provisioned it.

    The request body is never the source. An unset ``OSIRIS_AUDIT_ACTOR``
    still yields a server identity, and authentication stays false.
    """
    configured = os.getenv("OSIRIS_AUDIT_ACTOR", "").strip()
    if not configured:
        return "service:quant-api", False
    if _ACTOR_RE.fullmatch(configured) is None:
        raise ValueError("audit_actor_invalid")
    return configured, True


def stamp_server_event(event: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Copy ``event`` and replace linkage, actor, and timestamp with server values."""
    if not isinstance(event, dict):
        raise ValueError("event must be an object")
    actor, authenticated = server_actor()
    claimed_actor = event.get("actor")
    claimed_timestamp = event.get("timestamp")
    stamped = {
        key: value
        for key, value in event.items()
        if key not in {
            "sequence", "previous_hash", "hash", "event_hash", "actor", "actor_source",
            "claimed_actor", "claimed_timestamp", "actor_authentication_verified",
            "durable_storage", "timestamp",
        }
    }
    stamped["actor"] = actor
    stamped["actor_source"] = "server"
    stamped["actor_authentication_verified"] = authenticated
    stamped["timestamp"] = _server_timestamp(now)
    if isinstance(claimed_actor, str) and claimed_actor.strip() and claimed_actor != actor:
        stamped["claimed_actor"] = claimed_actor
    if isinstance(claimed_timestamp, str) and claimed_timestamp.strip():
        stamped["claimed_timestamp"] = claimed_timestamp
    return stamped


def load_signing_key() -> tuple[Ed25519PrivateKey | None, str | None]:
    """Read the Ed25519 seed. An unset path returns ``(None, None)`` and does not raise."""
    path = os.getenv("OSIRIS_AUDIT_SIGNING_KEY_FILE", "").strip()
    if not path:
        return None, None
    try:
        raw = _read_regular_file(path)
        text = raw.strip()
        if b"BEGIN" in text:
            key = load_pem_private_key(text, password=None)
            if not isinstance(key, Ed25519PrivateKey):
                return None, "key_unreadable"
            return key, None
        seed = _decode_material(text)
        if len(seed) != 32:
            return None, "key_unreadable"
        return Ed25519PrivateKey.from_private_bytes(seed), None
    except (OSError, ValueError, TypeError, UnsupportedAlgorithm, binascii.Error):
        return None, "key_unreadable"


def load_verifying_key(path: str | None = None) -> tuple[bytes | None, str | None]:
    """Read a raw 32-byte public key. The private key is not required."""
    candidate = (path or os.getenv("OSIRIS_AUDIT_VERIFYING_KEY_FILE", "")).strip()
    if candidate:
        try:
            raw = _read_regular_file(candidate)
            text = raw.strip()
            if b"BEGIN" in text:
                key = load_pem_public_key(text)
                if not isinstance(key, Ed25519PublicKey):
                    return None, "verifying_key_unreadable"
                return key.public_bytes(Encoding.Raw, PublicFormat.Raw), None
            decoded = _decode_material(text)
            if len(decoded) != 32:
                return None, "verifying_key_unreadable"
            Ed25519PublicKey.from_public_bytes(decoded)
            return decoded, None
        except (OSError, ValueError, TypeError, UnsupportedAlgorithm, binascii.Error):
            return None, "verifying_key_unreadable"
    private, error = load_signing_key()
    if private is None:
        return None, error
    return private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw), None


def checkpoint_evidence(
    chain: str, database: str, anchor_path: str, *, anchored_at: str | None = None,
) -> dict[str, Any]:
    """Write a checkpoint file outside the database and return the public anchor."""
    kind = _chain_kind(chain)
    inspected = _inspect(kind, database)
    if not inspected["present"]:
        raise ValueError("audit_chain_missing")
    if not inspected["linkage_ok"]:
        raise ValueError("cannot checkpoint a tampered audit chain")
    if inspected["event_count"] <= 0 or inspected["head_hash"] is None:
        raise ValueError("cannot checkpoint an empty audit chain")
    if _same_path(anchor_path, database):
        raise ValueError("anchor_must_be_outside_database")
    envelope = _build_envelope(kind, inspected, anchored_at=anchored_at)
    write_anchor(anchor_path, envelope)
    return envelope


def verify_evidence(
    chain: str,
    *,
    database: str | None = None,
    anchor: str | None = None,
    bundle: dict[str, Any] | None = None,
    caller_chain: dict[str, Any] | None = None,
    public_key_path: str | None = None,
) -> dict[str, Any]:
    """Fail closed. The status is ``ok``, ``tampered``, or ``unverifiable``."""
    kind = chain.strip() if isinstance(chain, str) else ""
    if kind == "phase15":
        return _verify_phase15(caller_chain)
    if kind == "phase18":
        return _verify_phase18(bundle, public_key_path)
    if kind not in {"paper", "phase16"}:
        return _result(
            status="unverifiable", chain=kind or "unknown", reason="unknown_chain",
            signature_status="unsigned",
        )
    stored = PAPER_CHAIN if kind == "paper" else PHASE16_CHAIN
    if not database:
        return _result(
            status="unverifiable", chain=stored, reason="chain_missing",
            signature_status="unsigned", durable_storage=False,
        )
    inspected = _inspect(stored, database)
    anchor_path = anchor if anchor is not None else os.getenv("OSIRIS_AUDIT_ANCHOR_PATH", "").strip()
    loaded = (
        _read_anchor(anchor_path, public_key_path) if anchor_path
        else {"present": False, "reason": "anchor_missing"}
    )
    return _judge(inspected, loaded)


def refresh_phase16_anchor(store: Any, sequence: int) -> str:
    """Refresh the external phase 16 anchor after a committed append. Never raises."""
    path = os.getenv("OSIRIS_AUDIT_ANCHOR_PATH", "").strip()
    if not path:
        return "skipped"
    every = os.getenv("OSIRIS_AUDIT_CHECKPOINT_EVERY", "").strip()
    if every:
        if not every.isascii() or not every.isdigit():
            return "error"
        interval = int(every)
        if interval <= 0:
            return "skipped"
        if (sequence + 1) % interval != 0:
            return "deferred"
    if _same_path(path, getattr(store, "database_path", "")):
        return "error"
    inspected = _inspect_phase16(store.database_path)
    if not inspected["linkage_ok"] or inspected["event_count"] <= 0:
        return "error"
    envelope = _build_envelope(PHASE16_CHAIN, inspected)
    write_anchor(path, envelope)
    return "unsigned" if envelope["signature_status"] == "unsigned" else "written"


def attach_bundle_signature(bundle: dict[str, Any]) -> dict[str, Any]:
    """Attach an Ed25519 signature over the bundle hash, or mark the bundle unsigned.

    Fields are added after ``bundle_hash`` is computed. Callers that recompute
    the bundle hash must ignore ``_SIGNATURE_FIELDS``.
    """
    binding = {
        "schema": BUNDLE_SIGNATURE_SCHEMA,
        "bundle_id": bundle.get("bundle_id"),
        "bundle_hash": bundle.get("bundle_hash"),
        "event_count": bundle.get("event_count"),
        "head_hash": bundle.get("head_hash"),
        "algorithm": "sha256",
        "live_execution_enabled": False,
        "live_trading_authorized": False,
    }
    key, error = load_signing_key()
    if key is None:
        bundle["signature_status"] = "unsigned"
        bundle["signed"] = False
        if error:
            bundle["key_error"] = error
        return bundle
    bundle["signature_envelope"] = sign_bundle(binding, key)
    bundle["signature_status"] = "signed"
    bundle["signed"] = True
    return bundle


def signature_state(
    envelope: object, binding_hash: object, public_key_path: str | None = None,
) -> str:
    """Return ``verified``, ``invalid``, ``unverified``, or ``unsigned``."""
    if not isinstance(envelope, dict) or not isinstance(envelope.get("signature"), str):
        return "unsigned"
    if envelope.get("signature", "").strip() == "":
        return "unsigned"
    public, _error = load_verifying_key(public_key_path)
    if public is None:
        return "unverified"
    try:
        payload = verify_bundle(envelope, public)
    except ValueError:
        return "invalid"
    if not isinstance(payload, dict) or payload.get("bundle_hash") != binding_hash:
        return "invalid"
    if payload.get("schema") != BUNDLE_SIGNATURE_SCHEMA:
        return "invalid"
    if payload.get("live_execution_enabled") is not False or payload.get("live_trading_authorized") is not False:
        return "invalid"
    return "verified"


def bundle_actors_authenticated(bundle: dict[str, Any]) -> bool:
    events = bundle.get("events")
    if not isinstance(events, list) or not events:
        return False
    for row in events:
        if not isinstance(row, dict):
            return False
        event = row.get("event")
        if not isinstance(event, dict):
            return False
        if event.get("actor_source") != "server" or event.get("actor_authentication_verified") is not True:
            return False
        if not isinstance(event.get("actor"), str) or not event["actor"].strip():
            return False
    return True


def write_anchor(path: str, envelope: dict[str, Any]) -> None:
    target = Path(path)
    if target.is_symlink():
        raise ValueError("anchor_symlink_refused")
    raw = canonical(envelope)
    if b"PRIVATE KEY" in raw:
        raise ValueError("anchor_refused_key_material")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    if temporary.is_symlink():
        raise ValueError("anchor_symlink_refused")
    temporary.write_bytes(raw)
    os.replace(temporary, target)


def _verify_phase15(caller_chain: dict[str, Any] | None) -> dict[str, Any]:
    from .phase15_audit_chain import verify_audit_chain

    if not isinstance(caller_chain, dict):
        return _result(
            status="unverifiable", chain="phase15_caller", reason="chain_missing",
            signature_status="unsigned", durable_storage=False,
            actor_source="caller", actor_authentication_verified=False,
        )
    try:
        linkage = verify_audit_chain(caller_chain)
    except ValueError:
        return _result(
            status="unverifiable", chain="phase15_caller", reason="chain_malformed",
            signature_status="unsigned", durable_storage=False,
            actor_source="caller", actor_authentication_verified=False,
        )
    status = "tampered" if linkage.get("status") != "valid" else "unverifiable"
    reason = "hash_chain_inconsistent" if status == "tampered" else "caller_supplied_chain"
    return _result(
        status=status, chain="phase15_caller", reason=reason,
        signature_status="unsigned", event_count=linkage.get("event_count", 0),
        head_hash=linkage.get("verified_head_hash"), durable_storage=False,
        actor_source="caller", actor_authentication_verified=False,
        issues=linkage.get("issues", []),
    )


def _verify_phase18(bundle: dict[str, Any] | None, public_key_path: str | None) -> dict[str, Any]:
    from .phase18_audit_bundle import verify_audit_bundle

    if not isinstance(bundle, dict):
        return _result(
            status="unverifiable", chain="phase18_bundle", reason="bundle_missing",
            signature_status="unsigned",
        )
    try:
        linkage = verify_audit_bundle(bundle)
    except ValueError:
        return _result(
            status="unverifiable", chain="phase18_bundle", reason="bundle_malformed",
            signature_status="unsigned",
        )
    state = signature_state(bundle.get("signature_envelope"), linkage.get("bundle_hash"), public_key_path)
    hash_issues = [issue for issue in linkage.get("issues", []) if issue != "signature_invalid"]
    if hash_issues:
        return _result(
            status="tampered", chain="phase18_bundle", reason="bundle_inconsistent",
            signature_status="unsigned", signed=False, event_count=linkage.get("event_count", 0),
            head_hash=linkage.get("head_hash"), issues=linkage.get("issues", []),
            actor_authentication_verified=False,
        )
    if state == "invalid":
        return _result(
            status="tampered", chain="phase18_bundle", reason="bad_signature",
            signature_status="invalid", signed=False, event_count=linkage.get("event_count", 0),
            head_hash=linkage.get("head_hash"), issues=linkage.get("issues", []),
            actor_authentication_verified=False,
        )
    if state != "verified":
        return _result(
            status="unverifiable", chain="phase18_bundle",
            reason="unsigned" if state == "unsigned" else "verifying_key_missing",
            signature_status=state if state in {"unsigned", "unverified"} else "unsigned",
            event_count=linkage.get("event_count", 0), head_hash=linkage.get("head_hash"),
            actor_authentication_verified=False,
        )
    if not bundle_actors_authenticated(bundle):
        return _result(
            status="unverifiable", chain="phase18_bundle", reason="actor_not_authenticated",
            signature_status="verified", signed=True, event_count=linkage.get("event_count", 0),
            head_hash=linkage.get("head_hash"), actor_source="server",
            actor_authentication_verified=False,
        )
    return _result(
        status="ok", chain="phase18_bundle", reason="matched",
        signature_status="verified", signed=True, event_count=linkage.get("event_count", 0),
        head_hash=linkage.get("head_hash"), actor_source="server",
        actor_authentication_verified=True, anchor_present=True,
    )


def _judge(inspected: dict[str, Any], loaded: dict[str, Any]) -> dict[str, Any]:
    chain = inspected["chain"]
    common = {
        "chain": chain,
        "event_count": inspected["event_count"],
        "head_hash": inspected["head_hash"],
        "durable_storage": inspected["present"],
        "issues": inspected["issues"],
        "actor_source": "server" if inspected["server_actor"] else "missing",
        "actor_authentication_verified": False,
    }
    if not inspected["present"]:
        return _result(status="unverifiable", reason=inspected["reason"], signature_status="unsigned", **common)
    if not inspected["linkage_ok"]:
        return _result(status="tampered", reason="hash_chain_inconsistent", signature_status="unsigned", **common)
    if not loaded.get("present"):
        return _result(
            status="unverifiable", reason=loaded.get("reason", "anchor_missing"),
            signature_status="unsigned", anchor_present=False, **common,
        )
    payload = loaded.get("payload")
    if not isinstance(payload, dict):
        return _result(
            status="unverifiable", reason="anchor_malformed", signature_status="unsigned",
            anchor_present=True, **common,
        )
    imprint = _rfc3161_issue(payload)
    if imprint == "mismatch":
        return _result(
            status="tampered", reason="rfc3161_imprint_mismatch", signature_status="unsigned",
            anchor_present=True, **common,
        )
    if imprint == "rejected":
        return _result(
            status="unverifiable", reason="rfc3161_stub_only", signature_status="unsigned",
            anchor_present=True, **common,
        )
    signature = loaded.get("signature_state")
    if signature == "invalid":
        return _result(
            status="tampered", reason=loaded.get("reason") or "bad_signature",
            signature_status="invalid", anchor_present=True, **common,
        )
    if signature != "verified":
        reason = "unsigned" if signature == "unsigned" else "verifying_key_missing"
        return _result(
            status="unverifiable", reason=reason,
            signature_status=signature if signature in {"unsigned", "unverified"} else "unsigned",
            anchor_present=True, **common,
        )
    compared = _compare_anchor(inspected, payload)
    if compared is not None:
        return _result(
            status="tampered", reason=compared, signature_status="verified", signed=True,
            anchor_present=True, checkpoint_sequence=payload.get("sequence"), **common,
        )
    if payload.get("actor_source") != "server" or payload.get("actor_authentication_verified") is not True:
        return _result(
            status="unverifiable", reason="actor_not_authenticated", signature_status="verified",
            signed=True, anchor_present=True, checkpoint_sequence=payload.get("sequence"),
            actor=payload.get("actor") if isinstance(payload.get("actor"), str) else None,
            **common,
        )
    if chain == PHASE16_CHAIN and not inspected["actor_authenticated"]:
        return _result(
            status="unverifiable", reason="actor_not_authenticated", signature_status="verified",
            signed=True, anchor_present=True, checkpoint_sequence=payload.get("sequence"),
            actor=payload.get("actor"), **common,
        )
    return _result(
        status="ok", reason="matched", signature_status="verified", signed=True,
        anchor_present=True, checkpoint_sequence=payload.get("sequence"),
        actor=payload.get("actor"), actor_source="server", actor_authentication_verified=True,
        event_count=inspected["event_count"], head_hash=inspected["head_hash"],
        durable_storage=True, issues=inspected["issues"], chain=chain,
    )


def _compare_anchor(inspected: dict[str, Any], payload: dict[str, Any]) -> str | None:
    sequence = payload.get("sequence")
    event_count = payload.get("event_count")
    head = payload.get("head_hash")
    if type(sequence) is not int or sequence < 0 or not isinstance(head, str) or type(event_count) is not int:
        return "anchor_mismatch"
    if payload.get("chain") != inspected["chain"] or payload.get("algorithm") != "sha256":
        return "anchor_mismatch"
    if payload.get("live_execution_enabled") is not False or payload.get("live_trading_authorized") is not False:
        return "anchor_mismatch"
    expected_count = sequence if inspected["chain"] == PAPER_CHAIN else sequence + 1
    if event_count != expected_count:
        return "anchor_mismatch"
    if inspected["event_count"] < event_count:
        return "truncated"
    recorded = inspected["hashes"].get(sequence)
    if recorded is None:
        return "truncated"
    if recorded != head:
        return "anchor_mismatch"
    return None


def _rfc3161_issue(payload: dict[str, Any]) -> str | None:
    stub = payload.get("rfc3161")
    if not isinstance(stub, dict):
        return "rejected"
    rest = {key: value for key, value in payload.items() if key != "rfc3161"}
    try:
        expected = digest(rest)
    except ValueError:
        return "rejected"
    if stub.get("message_imprint_sha256") != expected or stub.get("hash_algorithm") != "sha256":
        return "mismatch"
    if stub.get("status") != "stub" or stub.get("interface") != "rfc3161":
        return "rejected"
    if stub.get("tsa_contacted") is not False or stub.get("token") is not None:
        return "rejected"
    return None


def _build_envelope(chain: str, inspected: dict[str, Any], *, anchored_at: str | None = None) -> dict[str, Any]:
    actor, authenticated = server_actor()
    sequence = inspected["tip_sequence"]
    body = {
        "schema_version": 1,
        "chain": chain,
        "sequence": sequence,
        "event_count": inspected["event_count"],
        "head_hash": inspected["head_hash"],
        "anchored_at": anchored_at or datetime.now(UTC).isoformat(),
        "actor": actor,
        "actor_source": "server",
        "actor_authentication_verified": authenticated,
        "algorithm": "sha256",
        "content_origin": "server",
        "live_execution_enabled": False,
        "live_trading_authorized": False,
    }
    body["rfc3161"] = {
        "interface": "rfc3161",
        "status": "stub",
        "hash_algorithm": "sha256",
        "message_imprint_sha256": digest({key: value for key, value in body.items()}),
        "tsa_contacted": False,
        "token": None,
    }
    key, error = load_signing_key()
    if key is None:
        evidence = {
            "schema": "osiris.signed-evidence.v1",
            "payload": body,
            "sha256": digest(body),
            "signature": None,
        }
        signature_status = "unsigned"
        signed = False
    else:
        evidence = sign_bundle(body, key)
        signature_status = "signed"
        signed = True
        error = None
    envelope = {
        "schema": ANCHOR_SCHEMA,
        "chain": chain,
        "signature_status": signature_status,
        "signed": signed,
        "evidence": evidence,
    }
    if error:
        envelope["key_error"] = error
    return envelope


def _read_anchor(path: str, public_key_path: str | None = None) -> dict[str, Any]:
    if not path:
        return {"present": False, "reason": "anchor_missing"}
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        return {"present": False, "reason": "anchor_missing"}
    try:
        import json
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return {"present": False, "reason": "anchor_unreadable"}
    if not isinstance(document, dict) or document.get("schema") != ANCHOR_SCHEMA:
        return {"present": False, "reason": "anchor_malformed"}
    evidence = document.get("evidence")
    if not isinstance(evidence, dict):
        return {"present": False, "reason": "anchor_malformed"}
    signature = evidence.get("signature")
    payload = evidence.get("payload")
    try:
        digest_matches = isinstance(payload, dict) and digest(payload) == evidence.get("sha256")
    except ValueError:
        digest_matches = False
    if not digest_matches:
        return {
            "present": True, "payload": payload, "signature_state": "invalid",
            "reason": "anchor_digest_mismatch",
        }
    if not isinstance(signature, str) or not signature.strip():
        return {"present": True, "payload": payload, "signature_state": "unsigned"}
    public, _error = load_verifying_key(public_key_path)
    if public is None:
        return {
            "present": True, "payload": evidence.get("payload"), "signature_state": "unverified",
        }
    try:
        payload = verify_bundle(evidence, public)
    except ValueError:
        return {
            "present": True, "payload": evidence.get("payload"), "signature_state": "invalid",
            "reason": "bad_signature",
        }
    if document.get("chain") not in {None, payload.get("chain")}:
        return {"present": True, "payload": payload, "signature_state": "invalid", "reason": "anchor_mismatch"}
    return {"present": True, "payload": payload, "signature_state": "verified"}


def _inspect(chain: str, database: str) -> dict[str, Any]:
    if chain == PAPER_CHAIN:
        return _inspect_paper(database)
    return _inspect_phase16(database)


def _inspect_paper(database: str) -> dict[str, Any]:
    import sqlite3

    from .paper_execution import _row_hash

    empty = _empty_inspection(PAPER_CHAIN)
    path = Path(database)
    if path.is_symlink() or not path.is_file():
        return empty
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
    except sqlite3.Error:
        return empty
    try:
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT seq, client_order_id, event, payload, prev_hash, row_hash "
                "FROM paper_exec_audit ORDER BY seq"
            ).fetchall()
        except sqlite3.Error:
            return empty
    finally:
        connection.close()
    return _link_rows(
        PAPER_CHAIN,
        rows,
        start=1,
        genesis=_GENESIS,
        sequence_of=lambda row: int(row["seq"]),
        hash_of=lambda row: row["row_hash"],
        previous_of=lambda row: row["prev_hash"],
        expected_hash=lambda previous, sequence, row: _row_hash(
            previous, sequence, row["event"], row["client_order_id"], row["payload"],
        ),
        actor_of=lambda _row: (True, True),
    )


def _inspect_phase16(database: str) -> dict[str, Any]:
    import json
    import sqlite3

    from .phase15_audit_chain import _canonical_event

    empty = _empty_inspection(PHASE16_CHAIN)
    path = Path(database)
    if database == ":memory:" or path.is_symlink() or not path.is_file():
        return empty
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
    except sqlite3.Error:
        return empty
    try:
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT sequence, event_id, previous_hash, event_json, event_hash "
                "FROM audit_events ORDER BY sequence"
            ).fetchall()
        except sqlite3.Error:
            return empty
    finally:
        connection.close()

    def expected(previous: str, sequence: int, row: sqlite3.Row) -> str:
        import hashlib
        event = json.loads(row["event_json"])
        if not isinstance(event, dict) or event.get("event_id") != row["event_id"]:
            raise ValueError("event_id_mismatch")
        return hashlib.sha256(_canonical_event({
            "sequence": sequence, "previous_hash": previous, "event": event,
        })).hexdigest()

    def actor(row: sqlite3.Row) -> tuple[bool, bool]:
        try:
            event = json.loads(row["event_json"])
        except (ValueError, TypeError):
            return False, False
        server = (
            isinstance(event, dict)
            and event.get("actor_source") == "server"
            and isinstance(event.get("actor"), str)
            and bool(event["actor"].strip())
        )
        authenticated = server and event.get("actor_authentication_verified") is True
        return server, authenticated

    return _link_rows(
        PHASE16_CHAIN,
        rows,
        start=0,
        genesis=_PHASE16_GENESIS,
        sequence_of=lambda row: int(row["sequence"]),
        hash_of=lambda row: row["event_hash"],
        previous_of=lambda row: row["previous_hash"],
        expected_hash=expected,
        actor_of=actor,
    )


def _link_rows(chain, rows, *, start, genesis, sequence_of, hash_of, previous_of, expected_hash, actor_of):
    issues: list[dict[str, Any]] = []
    hashes: dict[int, str] = {}
    previous = genesis
    server_actor_ok = True
    authenticated = True
    linkage_ok = True
    for offset, row in enumerate(rows):
        expected_sequence = start + offset
        try:
            actual_sequence = sequence_of(row)
            digest_value = hash_of(row)
            previous_value = previous_of(row)
            calculated = expected_hash(previous, expected_sequence, row)
            server, auth = actor_of(row)
        except (TypeError, ValueError, KeyError, ArithmeticError):
            issues.append({"sequence": expected_sequence, "code": "invalid_event_payload"})
            linkage_ok = False
            server_actor_ok = False
            authenticated = False
            break
        if actual_sequence != expected_sequence:
            issues.append({"sequence": expected_sequence, "code": "sequence_mismatch"})
            linkage_ok = False
        if previous_value != previous:
            issues.append({"sequence": expected_sequence, "code": "previous_hash_mismatch"})
            linkage_ok = False
        if digest_value != calculated:
            issues.append({"sequence": expected_sequence, "code": "hash_mismatch"})
            linkage_ok = False
        if not server:
            server_actor_ok = False
        if not auth:
            authenticated = False
        if isinstance(digest_value, str):
            hashes[expected_sequence] = calculated if linkage_ok else digest_value
        previous = digest_value if isinstance(digest_value, str) else calculated
    if not rows:
        server_actor_ok = False
        authenticated = False
    tip = (start + len(rows) - 1) if rows else None
    head = hashes.get(tip) if tip is not None and linkage_ok else (previous if rows and linkage_ok else None)
    if rows and linkage_ok:
        head = previous
    return {
        "chain": chain,
        "present": True,
        "reason": "present",
        "linkage_ok": linkage_ok,
        "issues": issues,
        "event_count": len(rows),
        "tip_sequence": tip if tip is not None else -1,
        "head_hash": head,
        "hashes": hashes if linkage_ok else {},
        "server_actor": server_actor_ok and linkage_ok and bool(rows),
        "actor_authenticated": authenticated and linkage_ok and bool(rows),
    }


def _empty_inspection(chain: str) -> dict[str, Any]:
    return {
        "chain": chain, "present": False, "reason": "chain_missing", "linkage_ok": False,
        "issues": [], "event_count": 0, "tip_sequence": -1, "head_hash": None, "hashes": {},
        "server_actor": False, "actor_authenticated": False,
    }


def _result(**fields: Any) -> dict[str, Any]:
    status = fields.get("status")
    if status not in {"ok", "tampered", "unverifiable"}:
        status = "unverifiable"
    # ``signed`` reports the signature check. It is never true for an unsigned anchor.
    signed = fields.get("signed") is True and fields.get("signature_status") == "verified"
    return {
        "status": status,
        "chain": fields.get("chain"),
        "reason": fields.get("reason"),
        "event_count": fields.get("event_count", 0),
        "head_hash": fields.get("head_hash"),
        "checkpoint_sequence": fields.get("checkpoint_sequence"),
        "signature_status": fields.get("signature_status", "unsigned"),
        "signed": signed,
        "actor": fields.get("actor"),
        "actor_source": fields.get("actor_source", "missing"),
        "actor_authentication_verified": fields.get("actor_authentication_verified") is True and status == "ok",
        "durable_storage": fields.get("durable_storage") is True,
        "anchor_present": fields.get("anchor_present") is True,
        "trusted_timestamp_verified": False,
        "issues": fields.get("issues", []),
        "live_execution_enabled": False,
        "live_trading_authorized": False,
        "new_risk_allowed": False,
    }


def _chain_kind(chain: str) -> str:
    if chain == "paper":
        return PAPER_CHAIN
    if chain == "phase16":
        return PHASE16_CHAIN
    raise ValueError("unknown_audit_chain")


def _server_timestamp(now: datetime | None) -> str:
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("server_timestamp_must_be_aware")
    return current.astimezone(UTC).isoformat()


def _read_regular_file(path: str) -> bytes:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError("key_unreadable")
    return candidate.read_bytes()


def _decode_material(raw: bytes) -> bytes:
    if len(raw) == 32:
        return raw
    text = raw.decode("ascii").strip()
    return base64.b64decode(text, validate=True)


def _same_path(left: str, right: str) -> bool:
    if not left or not right:
        return False
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return False
