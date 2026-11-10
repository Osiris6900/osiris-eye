#!/usr/bin/env python3
"""Isolated real SQLite backup/restore drill for the OSIRIS paper ledger.

This deliberately exercises only a throwaway fixture on one CI runner.
It is not a production DR or independent-host failover test, and it is
never evidence of live broker execution or a T18 release signoff.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time

from osiris_api.paper_execution import (
    initialize_paper_account,
    reconcile,
    recover,
    snapshot,
    submit_paper_order,
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sqlite(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("sqlite_file_missing_or_empty")
    with sqlite3.connect(str(path)) as conn:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("sqlite_integrity_failed")


def assert_paper_only(payload: dict) -> None:
    if payload.get("live_execution") is not False:
        raise RuntimeError("live_execution_not_disabled")
    if payload.get("broker_order_submitted") is not False:
        raise RuntimeError("broker_order_submission_not_disabled")


def run(output: Path) -> dict:
    if os.getenv("OSIRIS_ENABLE_LIVE_EXECUTION"):
        raise RuntimeError("live_execution_environment_refused")
    output.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    observed = (now - timedelta(seconds=10)).isoformat()
    timestamp = now.isoformat()
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise RuntimeError("exact_git_commit_required") from exc
    if len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit):
        raise RuntimeError("invalid_git_commit")

    with tempfile.TemporaryDirectory(prefix="osiris-paper-dr-") as tmp:
        root = Path(tmp)
        primary = root / "primary.sqlite3"
        backup = output / "local-paper-backup.sqlite3"
        restored_copy = output / "local-paper-restored.sqlite3"
        standby = root / "standby.sqlite3"

        # Always isolate the exercise from any operator database.
        os.environ["OSIRIS_PAPER_EXECUTION_DB"] = str(primary)
        initialize_paper_account("1000")
        initial_fill = submit_paper_order(
            client_order_id="local-dr-fixture-1",
            symbol="BTC", side="BUY", quantity="1", price="100",
            fee="1", observed_at=observed, now=timestamp,
        )
        assert_paper_only(initial_fill)
        if initial_fill.get("status") != "filled":
            raise RuntimeError("fixture_order_not_filled")
        before = snapshot()
        assert_paper_only(before)
        if before["fill_count"] != 1 or not reconcile()["passed"]:
            raise RuntimeError("fixture_ledger_not_reconciled")

        # SQLite's online backup API captures a consistent database image.
        with sqlite3.connect(str(primary)) as original, sqlite3.connect(str(backup)) as dest:
            original.backup(dest)
        verify_sqlite(backup)
        backup_digest = digest(backup)

        # Destroy the throwaway primary DB, restore from backup and
        # reopen the real paper-ledger module (not a mock implementation).
        primary.unlink()
        restore_started = time.monotonic()
        shutil.copyfile(backup, primary)
        verify_sqlite(primary)
        recovery = recover()
        restored = snapshot()
        assert_paper_only(restored)
        if recovery.get("audit_ok") is not True or not reconcile()["passed"]:
            raise RuntimeError("restored_ledger_not_reconciled")
        if before != restored:
            raise RuntimeError("restored_ledger_mismatch")
        restore_ms = round((time.monotonic() - restore_started) * 1000, 3)
        shutil.copyfile(primary, restored_copy)
        if digest(restored_copy) != backup_digest:
            raise RuntimeError("restored_bytes_do_not_match_backup")

        # Simulate switching this *same runner* to a standby database path.
        # This is not independent-host, remote-service or production failover.
        shutil.copyfile(backup, standby)
        switched_at = time.monotonic()
        os.environ["OSIRIS_PAPER_EXECUTION_DB"] = str(standby)
        standby_recovery = recover()
        standby_before = snapshot()
        if standby_recovery.get("audit_ok") is not True or standby_before != before:
            raise RuntimeError("standby_recovery_mismatch")
        assert_paper_only(standby_before)
        second = submit_paper_order(
            client_order_id="local-dr-fixture-2",
            symbol="BTC", side="BUY", quantity="1", price="100",
            fee="0", observed_at=observed, now=timestamp,
        )
        assert_paper_only(second)
        after = snapshot()
        assert_paper_only(after)
        if second.get("status") != "filled" or after["fill_count"] != 2:
            raise RuntimeError("standby_paper_continuation_failed")
        if not reconcile()["passed"]:
            raise RuntimeError("standby_ledger_not_reconciled")
        failover_ms = round((time.monotonic() - switched_at) * 1000, 3)

        # Confirm the source primary remained unchanged by the standby drill.
        os.environ["OSIRIS_PAPER_EXECUTION_DB"] = str(primary)
        if snapshot() != before or not reconcile()["passed"]:
            raise RuntimeError("source_ledger_modified_by_standby")

        report = {
            "schema": "osiris.local-paper-ledger-dr.v1",
            "status": "passed",
            "commit_sha": commit,
            "github_run_url": (
                "https://github.com/Osiris6900/osiris-eye/actions/runs/"
                + os.environ["GITHUB_RUN_ID"]
                if os.environ.get("GITHUB_RUN_ID") else None
            ),
            "scope": "single-runner-temporary-sqlite-paper-ledger",
            "temporary_fixture_only": True,
            "independent_hosts_verified": False,
            "production_service_recovery_verified": False,
            "actual_backup_restore_tested": True,
            "same_runner_standby_switch_tested": True,
            "real_dr_failover_tested": False,
            "live_execution": False,
            "broker_order_submitted": False,
            "backup_sha256": backup_digest,
            "restored_sha256": digest(restored_copy),
            "backup_file": backup.name,
            "restored_file": restored_copy.name,
            "initial_fill_count": before["fill_count"],
            "restored_fill_count": restored["fill_count"],
            "standby_fill_count_after_resume": after["fill_count"],
            "audit_event_count": len(before["audit"]),
            "restore_elapsed_ms_local_only": restore_ms,
            "switch_elapsed_ms_local_only": failover_ms,
            "rto_slo_certified": False,
            "rpo_slo_certified": False,
            "t18_release_gate_accepted": False,
        }
    path = output / "local-paper-recovery-report.json"
    path.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    manifest = output / "sha256-manifest.txt"
    manifest.write_text(
        "".join(f"{digest(p)}  {p.name}\n" for p in sorted(output.iterdir()) if p != manifest),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.output_dir)
    print(json.dumps({k: v for k, v in report.items() if k not in ("backup_sha256", "restored_sha256")}, sort_keys=True))


if __name__ == "__main__":
    main()
