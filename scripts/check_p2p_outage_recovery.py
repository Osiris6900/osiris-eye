#!/usr/bin/env python3
"""Analyze sanitized, operator-supplied P2P outage probe samples.

Input JSON: {"samples":[{"elapsed_ms":0,"connected":true,"delivered":true}, ...]}
Samples must be sorted by elapsed_ms. Recovery is the first delivered sample
after a declared outage_start_ms, not proof of continuous availability.
No infrastructure is provisioned or contacted.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def analyze(data: dict, outage_start_ms: int, max_recovery_ms: int) -> dict:
    if type(outage_start_ms) is not int or outage_start_ms < 0:
        raise ValueError("outage_start_ms must be a nonnegative integer")
    if type(max_recovery_ms) is not int or max_recovery_ms <= 0:
        raise ValueError("max_recovery_ms must be positive")
    samples = data.get("samples") if isinstance(data, dict) else None
    if not isinstance(samples, list) or len(samples) < 2:
        raise ValueError("at least two samples required")
    last = -1
    pre_outage = False
    outage_observed = False
    recovery = None
    for sample in samples:
        if not isinstance(sample, dict):
            raise ValueError("invalid sample")
        elapsed = sample.get("elapsed_ms")
        if type(elapsed) is not int or elapsed <= last:
            raise ValueError("elapsed_ms must be strictly increasing integers")
        if type(sample.get("connected")) is not bool or type(sample.get("delivered")) is not bool:
            raise ValueError("connected and delivered must be booleans")
        if sample["delivered"] and not sample["connected"]:
            raise ValueError("delivered sample cannot claim disconnected state")
        last = elapsed
        if elapsed < outage_start_ms:
            pre_outage |= sample["delivered"]
        else:
            if not sample["delivered"] and recovery is None:
                outage_observed = True
            elif sample["delivered"] and outage_observed and recovery is None:
                recovery = elapsed - outage_start_ms
    if not pre_outage:
        raise ValueError("no successful pre-outage delivery baseline")
    if not outage_observed:
        raise ValueError("no observed outage sample")
    return {
        "status": "pass" if recovery is not None and recovery <= max_recovery_ms else "fail",
        "gate": "sampled_outage_recovery",
        "outage_start_ms": outage_start_ms,
        "recovery_observed": recovery is not None,
        "sampled_recovery_ms": recovery,
        "max_recovery_ms": max_recovery_ms,
        "sample_count": len(samples),
        "continuous_availability_verified": False,
        "independent_hosts_verified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--outage-start-ms", type=int, required=True)
    parser.add_argument("--max-recovery-ms", type=int, required=True)
    args = parser.parse_args()
    try:
        data = json.loads(args.evidence.read_text(encoding="utf-8"))
        result = analyze(data, args.outage_start_ms, args.max_recovery_ms)
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "fail", "reason": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
