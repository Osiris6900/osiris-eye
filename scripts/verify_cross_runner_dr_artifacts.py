#!/usr/bin/env python3
"""Fail-closed offline review of synthetic cross-job paper DR artifacts.

This checks report bytes and their own manifest; it cannot independently
attest separate physical hosts or production RTO/RPO. Never sign off T18.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
RUN = re.compile(r"https://github\.com/Osiris6900/osiris-eye/actions/runs/[1-9][0-9]*\Z")


def verify(directory: Path, *, expected_commit: str, expected_run_url: str) -> list[str]:
    errors: list[str] = []
    name = "paper-ci-cross-job-restore.json"
    report_path = directory / name
    digest_path = directory / (name + ".sha256")
    if not SHA40.fullmatch(expected_commit) or not RUN.fullmatch(expected_run_url):
        return ["invalid_expected_candidate_binding"]
    if not directory.is_dir() or set(p.name for p in directory.iterdir()) != {name, name + ".sha256"}:
        return ["missing_or_extra_artifact"]
    if any(p.is_symlink() or not p.is_file() for p in (report_path, digest_path)):
        return ["unsafe_artifact"]
    try:
        raw = report_path.read_bytes()
        declared = digest_path.read_text(encoding="utf-8")
        report = json.loads(raw)
    except (OSError, UnicodeError, ValueError):
        return ["unreadable_artifact"]
    if declared != hashlib.sha256(raw).hexdigest() + "  " + name + "\n":
        errors.append("manifest_mismatch")
    if not isinstance(report, dict):
        return errors + ["report_not_object"]
    required = {
        "schema": "osiris.cross-runner-paper-restore.v1",
        "status": "passed",
        "scope": "ci-artifact-transfer-between-separate-github-jobs",
        "source_commit_sha": expected_commit,
        "source_github_run_url": expected_run_url,
        "restored_ledger_reconciled": True,
        "artifact_backup_transport_tested": True,
        "independent_physical_hosts_verified": False,
        "production_service_recovery_verified": False,
        "rto_slo_certified": False,
        "rpo_slo_certified": False,
        "live_execution": False,
        "broker_order_submitted": False,
        "t18_release_gate_accepted": False,
    }
    for key, value in required.items():
        if type(report.get(key)) is not type(value) or report.get(key) != value:
            errors.append("invalid_" + key)
    if not isinstance(report.get("backup_sha256"), str) or not SHA256.fullmatch(report["backup_sha256"]):
        errors.append("invalid_backup_digest")
    initial = report.get("restored_initial_fill_count")
    continued = report.get("continued_fill_count")
    valid_initial = type(initial) is int and initial >= 1
    if not valid_initial:
        errors.append("invalid_initial_fill_count")
    if not valid_initial or type(continued) is not int or continued != initial + 1:
        errors.append("invalid_continuation_count")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-url", required=True)
    args = parser.parse_args()
    errors = verify(args.directory, expected_commit=args.commit, expected_run_url=args.run_url)
    print(json.dumps({
        "offline_artifact_verified": not errors, "errors": errors,
        "independent_physical_hosts_verified": False,
        "production_dr_verified": False, "t18_release_gate_accepted": False,
    }, sort_keys=True))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
