#!/usr/bin/env python3
"""Restore a verified throwaway paper-ledger backup in a *separate CI job*.

The source backup is generated on another GitHub-hosted job and transferred
using pinned artifact actions. This is a synthetic backup portability test,
not independently verified physical hosts, a running service, or production DR.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from osiris_api.paper_execution import (
    recover, reconcile, snapshot, submit_paper_order,
)
from scripts.run_paper_dr_exercise import assert_paper_only, verify_sqlite
from scripts.verify_paper_dr_artifacts import verify as verify_source


def restore_and_verify(source: Path, output: Path) -> dict:
    if os.getenv("OSIRIS_ENABLE_LIVE_EXECUTION"):
        raise RuntimeError("live_execution_environment_refused")
    problems = verify_source(source)
    if problems:
        raise RuntimeError("source_artifacts_invalid:" + ",".join(sorted(problems)))
    baseline = json.loads((source / "local-paper-recovery-report.json").read_text())
    if baseline.get("actual_backup_restore_tested") is not True:
        raise RuntimeError("source_backup_not_tested")
    backup = source / "local-paper-backup.sqlite3"
    digest = hashlib.sha256(backup.read_bytes()).hexdigest()

    original_db = os.environ.get("OSIRIS_PAPER_EXECUTION_DB")
    existed = "OSIRIS_PAPER_EXECUTION_DB" in os.environ
    try:
        with tempfile.TemporaryDirectory(prefix="osiris-ci-cross-runner-") as folder:
            destination = Path(folder) / "paper-ledger-restored.sqlite3"
            shutil.copyfile(backup, destination)
            verify_sqlite(destination)
            os.environ["OSIRIS_PAPER_EXECUTION_DB"] = str(destination)
            recovery = recover()
            before = snapshot()
            assert_paper_only(before)
            if (
                recovery.get("audit_ok") is not True
                or not reconcile().get("passed")
                or before.get("fill_count") != baseline.get("restored_fill_count")
                or len(before.get("audit", [])) != baseline.get("audit_event_count")
            ):
                raise RuntimeError("cross_job_restored_ledger_not_reconciled")
            now = datetime.now(timezone.utc)
            continued = submit_paper_order(
                client_order_id="cross-job-restore-continuation",
                symbol="BTC", side="BUY", quantity="1", price="100", fee="0",
                observed_at=(now - timedelta(seconds=10)).isoformat(),
                now=now.isoformat(),
            )
            assert_paper_only(continued)
            after = snapshot()
            assert_paper_only(after)
            if (
                continued.get("status") != "filled"
                or after.get("fill_count") != before["fill_count"] + 1
                or not reconcile().get("passed")
            ):
                raise RuntimeError("cross_job_paper_continuation_failed")
    finally:
        if existed:
            os.environ["OSIRIS_PAPER_EXECUTION_DB"] = original_db
        else:
            os.environ.pop("OSIRIS_PAPER_EXECUTION_DB", None)

    result = {
        "schema": "osiris.cross-runner-paper-restore.v1",
        "status": "passed",
        "scope": "ci-artifact-transfer-between-separate-github-jobs",
        "source_commit_sha": baseline["commit_sha"],
        "source_github_run_url": baseline.get("github_run_url"),
        "backup_sha256": digest,
        "restored_initial_fill_count": before["fill_count"],
        "continued_fill_count": after["fill_count"],
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
    output.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    output.write_bytes(data)
    output.with_suffix(output.suffix + ".sha256").write_text(
        hashlib.sha256(data).hexdigest() + "  " + output.name + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = restore_and_verify(args.source_dir, args.output)
    print(json.dumps({
        "status": result["status"],
        "scope": result["scope"],
        "t18_release_gate_accepted": False,
    }))


if __name__ == "__main__":
    main()
