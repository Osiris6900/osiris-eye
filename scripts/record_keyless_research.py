#!/usr/bin/env python3
"""Capture genuine keyless quotes under a frozen plan, or reproduce them offline."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/quant-api/src"))

from osiris_api.control_plane.database import canonical, digest  # noqa: E402
from osiris_api.keyless_research import (  # noqa: E402
    build_archive, capture_response, quote_tick, read_archive, research_plan,
    source_manifest, validate_product, verify_archive,
)


def _write(path: Path, value: object) -> None:
    with path.open("xb") as file:
        os.chmod(path, 0o600)
        file.write(canonical(value) + b"\n")


def capture(directory: Path, *, samples: int, interval: float, symbol: str) -> dict:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT):
        raise ValueError("commit_keyless_capture_source_before_execution")
    plan = research_plan(head, symbol=symbol, samples=samples,
                         interval_seconds=interval, created_at=datetime.now(UTC))
    frozen = digest(plan)
    code_before = source_manifest()
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    _write(directory / "plan.json", plan)
    records = []
    result = {"passed": False, "candidate_commit": head, "plan_sha256": frozen,
              "release_gate_accepted": False, "live_trading_authorized": False}
    try:
        with (directory / "capture-responses.jsonl").open("xb") as file:
            os.chmod(directory / "capture-responses.jsonl", 0o600)

            def record(kind):
                value = capture_response(symbol, kind)
                records.append(value)
                file.write(canonical(value) + b"\n")
                file.flush()
                return value

            validate_product(record("product"), plan)
            previous = -1
            attempts = 0
            while len(records) - 1 < samples:
                attempts += 1
                if attempts > samples * 3:
                    raise ValueError("insufficient_distinct_keyless_trades")
                if attempts > 1:
                    time.sleep(interval)
                value = record("ticker")
                _, trade = quote_tick(value, plan)
                if trade == previous:
                    # Keep the original response in the journal, but do not
                    # manufacture new observations from the same trade.
                    records.pop()
                    continue
                if trade < previous:
                    raise ValueError("keyless_trade_sequence_rollback")
                previous = trade
                print(json.dumps({"captured": len(records) - 1, "required": samples}), flush=True)
        if (read_archive(directory / "plan.json") != plan or digest(plan) != frozen
                or source_manifest() != code_before
                or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() != head
                or subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)):
            raise ValueError("keyless_plan_or_source_changed_during_capture")
        artifact = build_archive(plan, records, frozen_plan_sha256=frozen)
        verified = verify_archive(artifact, expected_commit=head, expected_sha256=artifact["archive_sha256"])
        _write(directory / "research-archive.json", artifact)
        result.update(passed=True, **verified)
    except Exception as error:
        result.update(error={"type": type(error).__name__, "message": str(error)})
    result["retained_responses"] = len(records)
    _write(directory / "capture-result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    record = sub.add_parser("capture")
    record.add_argument("--output-dir", required=True, type=Path)
    record.add_argument("--symbol", default="BTC-USD")
    record.add_argument("--samples", type=int, default=32)
    record.add_argument("--interval", type=float, default=3)
    verify = sub.add_parser("verify")
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--commit", required=True)
    verify.add_argument("--sha256", required=True, help="Trusted expected canonical archive digest")
    args = parser.parse_args()
    try:
        result = (capture(args.output_dir, samples=args.samples, interval=args.interval, symbol=args.symbol)
                  if args.operation == "capture" else
                  verify_archive(read_archive(args.archive), expected_commit=args.commit,
                                 expected_sha256=args.sha256))
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get("passed", result.get("consistent")) else 1
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"passed": False, "error": str(error), "release_gate_accepted": False}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
