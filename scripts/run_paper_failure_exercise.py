#!/usr/bin/env python3
"""Execute real code-path failure injection against an isolated paper SQLite ledger.

This exercise uses only temporary synthetic state. It is not a production
service drill, independent release signoff, broker OMS or live execution.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Iterator

from osiris_api.paper_execution import (
    initialize_paper_account, reconcile, recover, set_kill_switch, snapshot,
    submit_paper_order,
)
from check_paper_failure_matrix import SCENARIOS, validate

SHA40 = re.compile(r"^[a-f0-9]{40}$")
RUN_URL = re.compile(r"^https://github\.com/Osiris6900/osiris-eye/actions/runs/[1-9][0-9]*$")
NOW = datetime(2026, 11, 8, 16, 0, tzinfo=timezone.utc).isoformat()
FRESH = (datetime(2026, 11, 8, 16, 0, tzinfo=timezone.utc) -
         timedelta(seconds=30)).isoformat()
STALE = (datetime(2026, 11, 8, 16, 0, tzinfo=timezone.utc) -
         timedelta(hours=2)).isoformat()


@contextmanager
def isolated_ledger(db_path: Path) -> Iterator[None]:
    previous = os.environ.get("OSIRIS_PAPER_EXECUTION_DB")
    os.environ["OSIRIS_PAPER_EXECUTION_DB"] = str(db_path)
    try:
        initialize_paper_account("1000")
        yield
    finally:
        if previous is None:
            os.environ.pop("OSIRIS_PAPER_EXECUTION_DB", None)
        else:
            os.environ["OSIRIS_PAPER_EXECUTION_DB"] = previous


def order(order_id: str, *, observed_at: str = FRESH, **kwargs: object) -> dict:
    return submit_paper_order(
        client_order_id=order_id, symbol="BTC", side="BUY",
        quantity="1", price="100", fee="0", observed_at=observed_at,
        now=NOW, **kwargs,
    )


def check_paper_only(value: dict) -> None:
    if value.get("live_execution") is not False or value.get("broker_order_submitted") is not False:
        raise RuntimeError("paper_only_invariant_failed")


def run_failure_matrix(output_dir: Path, *, commit_sha: str, ci_run_url: str) -> dict:
    if not isinstance(commit_sha, str) or not SHA40.fullmatch(commit_sha):
        raise ValueError("exact_commit_required")
    if not isinstance(ci_run_url, str) or not RUN_URL.fullmatch(ci_run_url):
        raise ValueError("actual_ci_run_url_required")
    if os.getenv("OSIRIS_ENABLE_LIVE_EXECUTION"):
        raise RuntimeError("live_execution_environment_refused")
    output_dir.mkdir(parents=True, exist_ok=True)
    details: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="osiris-failure-fixture-") as workdir:
        root = Path(workdir)

        # A kill switch must block a new order even after an earlier paper fill.
        with isolated_ledger(root / "kill.sqlite3"):
            filled = order("first-paper-fill")
            check_paper_only(filled)
            if filled["status"] != "filled":
                raise RuntimeError("kill_fixture_not_filled")
            trip = set_kill_switch(True)
            check_paper_only(trip)
            blocked = order("after-kill")
            check_paper_only(blocked)
            state = snapshot()
            check_paper_only(state)
            if (
                blocked.get("status") != "risk_rejected"
                or blocked.get("reason") != "kill_switch_active"
                or state["fill_count"] != 1 or not reconcile()["passed"]
            ):
                raise RuntimeError("kill_switch_did_not_fail_closed")
            details["kill_switch"] = {
                "blocked_reason": blocked["reason"],
                "fills_before": 1, "fills_after": state["fill_count"],
                "risk_decision": blocked["status"],
                "audited": True,
            }

        with isolated_ledger(root / "stale.sqlite3"):
            blocked = order("stale-order", observed_at=STALE)
            check_paper_only(blocked)
            replay = order("stale-order", observed_at=FRESH)
            check_paper_only(replay)
            state = snapshot()
            check_paper_only(state)
            if (
                blocked.get("status") != "risk_rejected"
                or blocked.get("reason") != "stale_market_data"
                or replay.get("status") != "idempotent_replay"
                or replay.get("original_status") != "risk_rejected"
                or state["fill_count"] != 0 or not reconcile()["passed"]
            ):
                raise RuntimeError("stale_data_rejection_failed")
            details["stale_data_rejection"] = {
                "blocked_reason": blocked["reason"],
                "replay": replay["status"], "replay_original": replay["original_status"],
                "fills_before": 0, "fills_after": state["fill_count"],
                "audited": True,
            }

        with isolated_ledger(root / "restart.sqlite3"):
            crashed = False
            try:
                order("crash-before-commit", simulate_crash=True)
            except RuntimeError as exc:
                if str(exc) != "simulated_crash_before_commit":
                    raise
                crashed = True
            if not crashed:
                raise RuntimeError("failure_injection_not_triggered")
            at_fault = snapshot()
            check_paper_only(at_fault)
            if at_fault["fill_count"] != 0 or at_fault["order_count"] != 0:
                raise RuntimeError("crash_wrote_uncommitted_fill")
            recovered = recover()
            check_paper_only(recovered["snapshot"])
            if recovered.get("passed") is not True or not reconcile()["passed"]:
                raise RuntimeError("recovery_audit_failed")
            authorized_after_recovery = order("crash-before-commit")
            check_paper_only(authorized_after_recovery)
            after = snapshot()
            check_paper_only(after)
            if (
                authorized_after_recovery.get("status") != "filled"
                or after["fill_count"] != 1 or not reconcile()["passed"]
            ):
                raise RuntimeError("restart_recovery_failed")
            details["restart_recovery"] = {
                "injected_error": "simulated_crash_before_commit",
                "uncommitted_fills_at_failure": at_fault["fill_count"],
                "authorized_fills_after_verified_recovery": after["fill_count"],
                "audited": True,
            }

    scenarios = {
        name: {
            "passed": True,
            "commit_sha": commit_sha,
            "orders_accepted_after_failure": 0,
            "unreconciled_orders": 0,
            "test_command": (
                "python scripts/run_paper_failure_exercise.py --output-dir "
                "<isolated-artifact-dir> --commit-sha <exact-sha> "
                "--run-url <actual-run-url>"
            ),
            "ci_run_url": ci_run_url,
        }
        for name in SCENARIOS
    }
    report = {
        "schema": "osiris.paper_ledger_failure_exercise.v1",
        "commit_sha": commit_sha,
        "live_execution_enabled": False,
        "broker_order_submitted": False,
        "artifact_scope": "single_ci_runner_isolated_temporary_synthetic_sqlite",
        "actual_paper_ledger_code_invoked": True,
        "production_failover_tested": False,
        "live_broker_execution_tested": False,
        "t16_gate_accepted": False,
        "scenarios": scenarios,
        "details": details,
        "limitations": [
            "in-process calls and one temporary SQLite ledger per scenario",
            "not a service outage, live network or independent host test",
            "not an external risk/compliance signoff",
        ],
    }
    problems = validate(report)
    if problems:
        raise RuntimeError("failure_matrix_contract_invalid:" + ",".join(problems))
    path = output_dir / "paper-failure-matrix.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "sha256sum.txt").write_text(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--run-url", required=True)
    args = parser.parse_args()
    report = run_failure_matrix(
        args.output_dir, commit_sha=args.commit_sha, ci_run_url=args.run_url,
    )
    print(json.dumps({
        "passed": True, "commit_sha": report["commit_sha"],
        "scenarios": list(report["scenarios"]),
        "t16_gate_accepted": False, "live_execution_enabled": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
