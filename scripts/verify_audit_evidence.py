#!/usr/bin/env python3
"""Verify an audit chain, its external checkpoint, and Ed25519 signatures offline.

The signing key is never read here. Pass the public key with ``--public-key``
or ``OSIRIS_AUDIT_VERIFYING_KEY_FILE``. Exit 0 is ``ok``, 1 is ``tampered``,
and 2 is ``unverifiable``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "quant-api" / "src"))

from osiris_api.audit_evidence import checkpoint_evidence, verify_evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    checkpoint = commands.add_parser("checkpoint")
    checkpoint.add_argument("--chain", required=True, choices=["paper", "phase16"])
    checkpoint.add_argument("--database", required=True)
    checkpoint.add_argument("--anchor", required=True)

    verify = commands.add_parser("verify")
    verify.add_argument("--chain", required=True, choices=["paper", "phase16", "phase15", "phase18"])
    verify.add_argument("--database")
    verify.add_argument("--anchor")
    verify.add_argument("--bundle")
    verify.add_argument("--caller-chain")
    verify.add_argument("--public-key")
    args = parser.parse_args(argv)

    if args.command == "checkpoint":
        try:
            envelope = checkpoint_evidence(args.chain, args.database, args.anchor)
        except (OSError, ValueError, TypeError) as exc:
            print(json.dumps({
                "status": "unverifiable", "reason": str(exc), "signed": False,
                "live_execution_enabled": False, "live_trading_authorized": False,
            }))
            return 2
        print(json.dumps({
            "status": "checkpoint_written",
            "chain": envelope.get("chain"),
            "signature_status": envelope.get("signature_status"),
            "signed": envelope.get("signed") is True,
            "anchor": args.anchor,
            "live_execution_enabled": False,
            "live_trading_authorized": False,
        }))
        return 0 if envelope.get("signed") is True else 2

    bundle = _load(args.bundle) if args.bundle else None
    caller = _load(args.caller_chain) if args.caller_chain else None
    if args.bundle and bundle is None or args.caller_chain and caller is None:
        print(json.dumps({
            "status": "unverifiable", "reason": "evidence_file_unreadable", "signed": False,
            "live_execution_enabled": False, "live_trading_authorized": False,
        }))
        return 2
    result = verify_evidence(
        args.chain, database=args.database, anchor=args.anchor, bundle=bundle,
        caller_chain=caller, public_key_path=args.public_key,
    )
    print(json.dumps(result))
    if result["status"] == "ok":
        return 0
    if result["status"] == "tampered":
        return 1
    return 2


def _load(path: str) -> dict | None:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


if __name__ == "__main__":
    sys.exit(main())
