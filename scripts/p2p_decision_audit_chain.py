#!/usr/bin/env python3
"""Offline tamper-evident hash-chain for P2P intelligence decisions.

Hashes detect accidental/unauthorized modification against a trusted anchor.
They are not signatures, trusted timestamps, or immutable storage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SCHEMA = "osiris.p2p.decision_audit.v1"
GENESIS = "0" * 64


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def append(entries: list[dict], event: dict) -> list[dict]:
    if verify(entries)["status"] != "valid":
        raise ValueError("existing_chain_invalid")
    if not isinstance(event, dict) or set(event) != {
        "decision_id", "peer_id", "observed_at_ms", "decision", "evidence_sha256"
    }:
        raise ValueError("invalid_event_shape")
    if (not isinstance(event["decision_id"], str) or not event["decision_id"].strip()
            or not isinstance(event["peer_id"], str) or not event["peer_id"].strip()
            or type(event["observed_at_ms"]) is not int or event["observed_at_ms"] < 0
            or event["decision"] not in ("observe", "investigate", "route_advisory", "block_advisory")
            or not isinstance(event["evidence_sha256"], str)
            or len(event["evidence_sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in event["evidence_sha256"])):
        raise ValueError("invalid_event_fields")
    if any(item["event"]["decision_id"] == event["decision_id"] for item in entries):
        raise ValueError("duplicate_decision_id")
    previous = entries[-1]["entry_sha256"] if entries else GENESIS
    record = {"schema": SCHEMA, "index": len(entries), "previous_sha256": previous, "event": event}
    record["entry_sha256"] = hashlib.sha256(canonical(record)).hexdigest()
    return [*entries, record]


def verify(entries: object, expected_tip: str | None = None) -> dict:
    if not isinstance(entries, list) or len(entries) > 100000:
        return {"status": "invalid", "reason": "invalid_chain_length"}
    prior, ids = GENESIS, set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != {
            "schema", "index", "previous_sha256", "event", "entry_sha256"
        }:
            return {"status": "invalid", "reason": "invalid_record_shape", "index": index}
        if (entry["schema"] != SCHEMA or type(entry["index"]) is not int
                or entry["index"] != index or entry["previous_sha256"] != prior):
            return {"status": "invalid", "reason": "chain_link_mismatch", "index": index}
        event = entry["event"]
        if not isinstance(event, dict) or not isinstance(event.get("decision_id"), str) or event["decision_id"] in ids:
            return {"status": "invalid", "reason": "duplicate_or_invalid_event", "index": index}
        ids.add(event["decision_id"])
        try:
            expected = hashlib.sha256(canonical({k: entry[k] for k in (
                "schema", "index", "previous_sha256", "event"
            )})).hexdigest()
        except (ValueError, TypeError):
            return {"status": "invalid", "reason": "noncanonical_event", "index": index}
        if entry["entry_sha256"] != expected:
            return {"status": "invalid", "reason": "entry_hash_mismatch", "index": index}
        prior = expected
    if expected_tip is not None and expected_tip != prior:
        return {"status": "invalid", "reason": "trusted_tip_mismatch"}
    return {"status": "valid", "entries": len(entries), "tip_sha256": prior,
            "externally_anchored": expected_tip is not None,
            "signed": False, "immutable_storage_verified": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chain", type=Path)
    parser.add_argument("--expected-tip")
    args = parser.parse_args()
    try:
        result = verify(json.loads(args.chain.read_text(encoding="utf-8")), args.expected_tip)
    except (OSError, ValueError) as exc:
        result = {"status": "invalid", "reason": str(exc)}
    print(json.dumps(result, sort_keys=True))
    return int(result["status"] != "valid")


if __name__ == "__main__":
    raise SystemExit(main())
