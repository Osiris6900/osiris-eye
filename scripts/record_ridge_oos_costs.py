#!/usr/bin/env python3
"""Freeze, evaluate and verify post-capture fitted-ridge cost diagnostics offline."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/quant-api/src"))
from osiris_api.control_plane.database import canonical, digest  # noqa: E402
from osiris_api.keyless_research import read_archive  # noqa: E402
from osiris_api.ridge_oos_costs import build_plan, evaluate_archive, verify_report  # noqa: E402


def _write(path, value):
    if path.resolve().is_relative_to(ROOT):
        raise ValueError("ridge_evidence_must_be_outside_checkout")
    with path.open("xb") as file:
        os.chmod(path, 0o600)
        file.write(canonical(value) + b"\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--archive", type=Path, required=True)
    plan.add_argument("--archive-commit", required=True)
    plan.add_argument("--archive-sha256", required=True)
    plan.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("evaluate")
    run.add_argument("--archive", type=Path, required=True)
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--plan-sha256", required=True)
    run.add_argument("--output", type=Path, required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--report", type=Path, required=True)
    verify.add_argument("--sha256", required=True)
    verify.add_argument("--analysis-commit", required=True)
    args = parser.parse_args()
    try:
        archive = read_archive(args.archive)
        if args.operation == "verify":
            result = verify_report(archive, read_archive(args.report),
                expected_sha256=args.sha256, analysis_commit=args.analysis_commit)
        else:
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
            if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT):
                raise ValueError("commit_ridge_analysis_source_before_execution")
            if args.operation == "plan":
                value = build_plan(archive, archive_commit=args.archive_commit,
                    archive_sha256=args.archive_sha256, analysis_commit=head, created_at=datetime.now(UTC))
                result = {"plan_sha256": digest(value), "release_gate_accepted": False}
            else:
                plan = read_archive(args.plan)
                if not isinstance(plan, dict) or plan.get("analysis_commit") != head:
                    raise ValueError("ridge_analysis_commit_mismatch")
                value = evaluate_archive(archive, plan, expected_plan_sha256=args.plan_sha256)
                result = {"report_sha256": value["report_sha256"], "folds": len(value["folds"]),
                          "release_gate_accepted": False}
            _write(args.output, value)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"passed": False, "error": str(error), "release_gate_accepted": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
