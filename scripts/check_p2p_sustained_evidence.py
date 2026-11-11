#!/usr/bin/env python3
"""Review one opt-in sustained 100-peer star report; never certify T04/T19.

The workflow can validate local JSON structure and policy thresholds only.
A reviewer must independently verify GitHub runs, downloaded artifact bytes,
host independence, NAT/relay topology and operational recovery.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from check_p2p_load_acceptance import POLICY_PATH, check

_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RUN = re.compile(r"https://github\.com/Osiris6900/osiris-eye/actions/runs/[1-9][0-9]*\Z")
_HOSTS = {"host-a", "host-b", "host-c"}


def validate(data: object, *, commit: str, run_url: str, attempt: int, policy: dict) -> dict:
    errors: list[str] = []
    if not isinstance(commit, str) or not _SHA40.fullmatch(commit):
        errors.append("expected_commit_invalid")
    if not isinstance(run_url, str) or not _RUN.fullmatch(run_url):
        errors.append("expected_run_url_invalid")
    if type(attempt) is not int or attempt < 1:
        errors.append("expected_attempt_invalid")
    if not isinstance(data, dict):
        data = {}
        errors.append("report_not_object")
    if data.get("commit_sha") != commit or data.get("clean_worktree") is not True:
        errors.append("candidate_not_exact_clean_commit")
    if data.get("workflow_url") != run_url or data.get("attempt") != attempt:
        errors.append("run_binding_mismatch")
    if data.get("measurement_class") != "operator_attested_real_multi_host":
        errors.append("multi_host_class_not_reported")
    workload = data.get("workload")
    if not isinstance(workload, dict) or (
        workload.get("peers") != 100 or workload.get("messages") != 10000
        or workload.get("fanout") != 1
    ):
        errors.append("sustained_star_workload_mismatch")
    elif (not isinstance(workload.get("duration_seconds"), (int, float))
          or isinstance(workload["duration_seconds"], bool)
          or workload["duration_seconds"] < 900):
        errors.append("sustained_duration_below_900s")
    hosts = data.get("hosts")
    if not isinstance(hosts, list) or len(hosts) != 3:
        errors.append("exactly_three_host_records_required")
    else:
        ids: set[str] = set()
        commitments: set[str] = set()
        for host in hosts:
            if not isinstance(host, dict):
                errors.append("host_record_invalid")
                continue
            ids.add(host.get("host_id"))
            digest = host.get("host_fingerprint_commitment")
            if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                errors.append("host_commitment_invalid")
            else:
                commitments.add(digest)
            binary = host.get("binary_sha256")
            if not isinstance(binary, str) or not _SHA256.fullmatch(binary):
                errors.append("binary_digest_invalid")
        if ids != _HOSTS or len(commitments) != 3:
            errors.append("distinct_three_hosts_not_reported")
    notes = data.get("measurement_notes")
    if not isinstance(notes, dict) or (
        notes.get("open_mesh_capacity_tested") is not False
        or notes.get("workload_paused_during_outage") is not True
        or notes.get("independent_host_fingerprints_distinct") is not True
    ):
        errors.append("declared_star_and_outage_limits_missing")

    policy_result = None
    try:
        policy_result = check(data, policy)
    except (ValueError, TypeError, KeyError) as exc:
        errors.append("load_policy_shape_invalid:" + str(exc))
    if policy_result is None or policy_result["status"] != "pass":
        errors.append("predeclared_load_policy_not_met")
    return {
        "schema": "osiris.p2p_sustained_offline_review.v1",
        "passed_offline_consistency": not errors,
        "errors": errors,
        "exact_commit_claimed": commit,
        "workflow_url_claimed": run_url,
        "attempt_claimed": attempt,
        "load_policy_verdict": policy_result,
        "independent_host_identity_verified": False,
        "github_workflow_conclusion_verified": False,
        "production_network_or_open_mesh_verified": False,
        "t04_t19_release_gates_accepted": False,
        "independent_review_required": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        data_bytes = args.report.read_bytes()
        data = json.loads(data_bytes)
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        result = validate(data, commit=args.commit, run_url=args.run_url,
                          attempt=args.attempt, policy=policy)
        result["raw_report_sha256"] = hashlib.sha256(data_bytes).hexdigest()
    except (ValueError, OSError, TypeError) as exc:
        result = {
            "schema": "osiris.p2p_sustained_offline_review.v1",
            "passed_offline_consistency": False,
            "errors": ["cannot_read_or_parse_report:" + str(exc)],
            "t04_t19_release_gates_accepted": False,
            "independent_review_required": True,
        }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed_offline_consistency"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
