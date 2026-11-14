#!/usr/bin/env python3
"""Explicit one-shot BLS macro capture, offline verification and gated ingestion."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/quant-api/src"))

from osiris_api.data_store import ResearchStore
from osiris_api.macro_economic_watch import (
    SERIES,
    capture_bls,
    ingest_bls_capture,
    preflight_bls_ingestion,
    verify_bls_capture,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    capture = sub.add_parser("capture")
    capture.add_argument("--output-dir", required=True, type=Path)
    capture.add_argument("--series", nargs="+", choices=sorted(SERIES), required=True)
    for name in ("verify", "ingest"):
        command = sub.add_parser(name)
        command.add_argument("--capture-dir", required=True, type=Path)
        command.add_argument("--commit", required=True)
        command.add_argument("--response-sha256", required=True)
        command.add_argument("--capture-sha256", required=True)
        if name == "ingest":
            command.add_argument("--database", required=True, type=Path)
            command.add_argument("--policy-json", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.operation == "capture":
            if args.output_dir.resolve().is_relative_to(ROOT):
                raise ValueError("bls_capture_must_be_outside_source")
            if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT):
                raise ValueError("commit_bls_source_before_capture")
            head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip()

            def unchanged():
                return (
                    not subprocess.check_output(
                        ["git", "status", "--porcelain"], cwd=ROOT
                    )
                    and subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                    ).strip()
                    == head
                )

            result = capture_bls(
                args.output_dir,
                candidate_commit=head,
                series=args.series,
                source_guard=unchanged,
            )
        elif args.operation == "verify":
            result = verify_bls_capture(
                args.capture_dir,
                expected_commit=args.commit,
                expected_response_sha256=args.response_sha256,
                expected_capture_sha256=args.capture_sha256,
            )
        else:
            policy = json.loads(args.policy_json.read_bytes())
            current = datetime.now(UTC)
            preflight_bls_ingestion(
                args.capture_dir,
                expected_commit=args.commit,
                expected_response_sha256=args.response_sha256,
                expected_capture_sha256=args.capture_sha256,
                policy=policy,
                now=current,
            )
            result = ingest_bls_capture(
                ResearchStore(str(args.database)),
                args.capture_dir,
                expected_commit=args.commit,
                expected_response_sha256=args.response_sha256,
                expected_capture_sha256=args.capture_sha256,
                policy=policy,
                now=current,
            )
        print(json.dumps(result, sort_keys=True))
        return 0 if result["passed"] else 1
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as error:
        print(
            json.dumps(
                {
                    "passed": False,
                    "error": str(error),
                    "release_gate_accepted": False,
                    "live_trading_authorized": False,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
