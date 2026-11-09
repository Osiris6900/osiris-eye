#!/usr/bin/env python3
"""One-command fail-closed verification for Osiris Eye.

The default is a dry run. Pass --execute to run the checks. This does not
upgrade dependencies, download artifacts, submit broker orders, commit, or merge.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
CHECKS = [
    ("python-tests", ["python", "-m", "pytest", "-q", "services/quant-api/tests"], None),
    ("release-inventory", ["python", "scripts/check_release_gate_status.py"], None),
    ("release-strict", ["python", "scripts/check_release_gate_status.py", "--strict"], None),
    ("p2p-workflows", ["python", "scripts/check_p2p_workflows.py"], None),
    ("rust-risk", ["cargo", "test", "--manifest-path", "crates/risk-kernel/Cargo.toml"], None),
    ("rust-p2p", ["cargo", "test", "--locked", "--manifest-path", "services/p2p-node/Cargo.toml", "--all-targets"], None),
    ("frontend-typecheck", ["npm", "run", "typecheck"], "apps/web"),
    ("frontend-tests", ["npm", "test", "--", "--run"], "apps/web"),
    ("frontend-build", ["npm", "run", "build"], "apps/web"),
]


def run_check(name, command, cwd, timeout):
    executable = shutil.which(command[0])
    if executable is None:
        return {"name": name, "status": "blocked", "reason": "tool unavailable: " + command[0]}
    try:
        result = subprocess.run(command, cwd=ROOT / cwd if cwd else ROOT,
                                capture_output=True, text=True, timeout=timeout,
                                check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"name": name, "status": "blocked", "reason": str(exc)}
    return {"name": name, "status": "passed" if result.returncode == 0 else "failed",
            "exit_code": result.returncode,
            "output_tail": (result.stdout + "\n" + result.stderr)[-4000:]}


def build_report(results):
    """A local verification report never authorizes live trading or a merge."""
    return {
        "schema": "osiris.verification.v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "live_trading_authorized": False,
        "merge_authorized": False,
        "checks": results,
        "all_passed": bool(results) and all(item["status"] == "passed" for item in results),
        "external_evidence_required": [
            "independent-host 100-peer soak", "cryptographic security audit",
            "deployment and recovery evidence", "GitHub CI and required reviews",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Show checks without executing")
    parser.add_argument("--execute", action="store_true", help="Run the checks. Omit to dry-run.")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--report", type=Path, help="Optional JSON report path")
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    if args.execute and args.dry_run:
        parser.error("pass only one of --execute and --dry-run")
    if not (ROOT / "crates/risk-kernel/Cargo.toml").is_file():
        parser.error("Run from an Osiris Eye checkout containing the risk kernel")
    if not (ROOT / "services/quant-api/tests").is_dir():
        parser.error("paper and API tests are missing")
    if not args.execute:
        for name, command, cwd in CHECKS:
            print(f"PLAN {name}: {command} (cwd={cwd or '.'})")
        return 0

    results = [run_check(name, command, cwd, args.timeout) for name, command, cwd in CHECKS]
    report = build_report(results)
    print(json.dumps(report, indent=2))
    if args.report:
        path = args.report.resolve()
        if path.exists():
            parser.error("report path exists; refusing overwrite")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
