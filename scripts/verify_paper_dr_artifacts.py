#!/usr/bin/env python3
"""Verify retained synthetic paper DR artifacts; never assert production recovery."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def verify(directory: Path) -> list[str]:
    errors = []
    report_path = directory / "local-paper-recovery-report.json"
    manifest_path = directory / "sha256-manifest.txt"
    if not report_path.is_file() or not manifest_path.is_file():
        return ["missing_report_or_manifest"]
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return ["invalid_report"]
    if not isinstance(report, dict):
        return ["invalid_report"]
    allowed_names = {"local-paper-recovery-report.json", "sha256-manifest.txt",
                     "local-paper-backup.sqlite3", "local-paper-restored.sqlite3"}
    if any(p.is_symlink() or not p.is_file() or p.name not in allowed_names
           for p in directory.iterdir()):
        errors.append("unexpected_or_unsafe_artifact")
    expected = {
        "schema": "osiris.local-paper-ledger-dr.v1",
        "status": "passed",
        "scope": "single-runner-temporary-sqlite-paper-ledger",
        "temporary_fixture_only": True,
        "independent_hosts_verified": False,
        "production_service_recovery_verified": False,
        "real_dr_failover_tested": False,
        "live_execution": False,
        "broker_order_submitted": False,
        "t18_release_gate_accepted": False,
    }
    for key, value in expected.items():
        if report.get(key) != value:
            errors.append("invalid_report_" + key)
    commit = report.get("commit_sha")
    if not isinstance(commit, str) or len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        errors.append("invalid_commit_sha")
    files = ("local-paper-backup.sqlite3", "local-paper-restored.sqlite3")
    for field, name in (("backup_file", files[0]), ("restored_file", files[1])):
        if report.get(field) != name:
            errors.append("invalid_report_" + field)
    for name, field in zip(files, ("backup_sha256", "restored_sha256")):
        path = directory / name
        if not path.is_file():
            errors.append("missing_" + name)
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != report.get(field):
            errors.append("digest_mismatch_" + name)
    if report.get("backup_sha256") != report.get("restored_sha256"):
        errors.append("backup_restore_digest_mismatch")
    expected_lines = []
    for path in sorted(directory.iterdir()):
        if path.is_file() and path.name != manifest_path.name:
            expected_lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")
    if manifest_path.read_text(encoding="utf-8") != "".join(expected_lines):
        errors.append("manifest_mismatch")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_directory", type=Path)
    args = parser.parse_args()
    errors = verify(args.artifact_directory)
    print(json.dumps({"verified": not errors, "errors": errors, "production_dr_verified": False}))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
