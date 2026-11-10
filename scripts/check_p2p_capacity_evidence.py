#!/usr/bin/env python3
"""Offline capacity-artifact integrity checks; never a T04/T19 signoff.

This checks JSON measurements and locally hashes their bytes. It cannot
authenticate GitHub Actions run status, independent physical host ownership,
or the claims inside a self-reported measurement; external review is required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re

SWEEPS = (10, 50, 100)
HOSTS = frozenset(("host-a", "host-b", "host-c"))
_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RUN = re.compile(
    r"https://github\.com/Osiris6900/osiris-eye/actions/runs/[1-9][0-9]*\Z"
)


def _finite_nonnegative(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate_reports(
    reports: dict[int, object], *,
    expected_commit: str, expected_run_url: str, expected_attempt: int,
) -> dict:
    """Validate three raw reports and return only a REVIEW-REQUIRED verdict."""
    errors: list[str] = []
    degraded: list[int] = []
    commitments: dict[int, dict[str, str]] = {}
    if not _SHA40.fullmatch(expected_commit):
        errors.append("invalid_expected_commit")
    if not _RUN.fullmatch(expected_run_url):
        errors.append("invalid_expected_run_url")
    if not isinstance(expected_attempt, int) or isinstance(expected_attempt, bool) or expected_attempt < 1:
        errors.append("invalid_expected_attempt")
    if set(reports) != set(SWEEPS):
        errors.append("exact_10_50_100_reports_required")

    for peers in SWEEPS:
        report = reports.get(peers)
        label = str(peers) + ":"
        if not isinstance(report, dict):
            errors.append(label + "missing_json_object")
            continue
        if report.get("schema") != "osiris.p2p_load_acceptance.v1":
            errors.append(label + "schema_invalid")
        if report.get("measurement_class") != "operator_attested_real_multi_host":
            errors.append(label + "independent_multi_host_not_reported")
        if report.get("clean_worktree") is not True:
            errors.append(label + "worktree_not_clean")
        if report.get("commit_sha") != expected_commit:
            errors.append(label + "commit_mismatch")
        if report.get("workflow_url") != expected_run_url:
            errors.append(label + "run_url_mismatch")
        if report.get("attempt") != expected_attempt:
            errors.append(label + "run_attempt_mismatch")
        workload = report.get("workload")
        if not isinstance(workload, dict):
            errors.append(label + "workload_missing")
        else:
            if workload.get("peers") != peers:
                errors.append(label + "peer_count_mismatch")
            if workload.get("messages") != 200:
                errors.append(label + "expected_200_workload_messages")
            if not _finite_nonnegative(workload.get("duration_seconds")) or workload["duration_seconds"] == 0:
                errors.append(label + "duration_invalid")
            if workload.get("fanout") != 1:
                errors.append(label + "fanout_not_one")
        notes = report.get("measurement_notes")
        if not isinstance(notes, dict):
            errors.append(label + "measurement_notes_missing")
        else:
            if notes.get("independent_host_fingerprints_distinct") is not True:
                errors.append(label + "host_independence_not_reported")
            if notes.get("open_mesh_capacity_tested") is not False:
                errors.append(label + "topology_scope_mismatch")
            if notes.get("workload_paused_during_outage") is not True:
                errors.append(label + "outage_scope_mismatch")
        hosts = report.get("hosts")
        if not isinstance(hosts, list) or len(hosts) != 3:
            errors.append(label + "exact_three_hosts_required")
        else:
            host_commitments: dict[str, str] = {}
            for host in hosts:
                if not isinstance(host, dict):
                    errors.append(label + "host_record_invalid")
                    continue
                name = host.get("host_id")
                binary = host.get("binary_sha256")
                commitment = host.get("host_fingerprint_commitment")
                if not isinstance(name, str) or name not in HOSTS or name in host_commitments:
                    errors.append(label + "host_id_invalid_or_duplicate")
                    continue
                if not isinstance(binary, str) or not _SHA256.fullmatch(binary):
                    errors.append(label + "binary_sha256_missing")
                if not isinstance(commitment, str) or not _SHA256.fullmatch(commitment):
                    errors.append(label + "host_fingerprint_commitment_invalid")
                else:
                    host_commitments[name] = commitment
            if set(host_commitments) != HOSTS or len(set(host_commitments.values())) != 3:
                errors.append(label + "distinct_host_commitments_not_proved")
            else:
                commitments[peers] = host_commitments

        delivery = report.get("delivery")
        records = report.get("delivery_records")
        latency = report.get("latency_ms")
        if not isinstance(delivery, dict) or not isinstance(records, list) or not isinstance(latency, dict):
            errors.append(label + "delivery_or_latency_missing")
        else:
            sent, delivered, lost = (delivery.get(field) for field in ("sent", "delivered", "lost"))
            if not all(_is_int(v) for v in (sent, delivered, lost)):
                errors.append(label + "delivery_counters_invalid")
            elif not (
                sent == 200 and sent == delivered + lost
                and delivery.get("pending") == 0 and delivery.get("rejected") == 0
                and len(records) == sent
            ):
                errors.append(label + "delivery_accounting_mismatch")
            else:
                seen: set[str] = set()
                observed_delivered = 0
                observed_lost = 0
                for record in records:
                    if not isinstance(record, dict):
                        errors.append(label + "delivery_record_invalid")
                        break
                    digest = record.get("payload_sha256")
                    if not isinstance(digest, str) or not _SHA256.fullmatch(digest) or digest in seen:
                        errors.append(label + "delivery_digest_invalid_or_duplicate")
                        break
                    seen.add(digest)
                    outcome = record.get("outcome")
                    if outcome == "delivered" and _finite_nonnegative(record.get("latency_ms")):
                        observed_delivered += 1
                    elif outcome == "lost" and record.get("latency_ms") is None:
                        observed_lost += 1
                    else:
                        errors.append(label + "delivery_outcome_or_latency_invalid")
                        break
                if observed_delivered != delivered or observed_lost != lost:
                    errors.append(label + "delivery_records_disagree_with_counters")
                if latency.get("count") != delivered:
                    errors.append(label + "latency_count_mismatch")
                values = [latency.get(k) for k in ("p50", "p95", "p99", "max")]
                if not all(_finite_nonnegative(v) for v in values) or values != sorted(values):
                    errors.append(label + "latency_percentiles_invalid")
                if lost:
                    degraded.append(peers)
        outage = report.get("outage")
        if not isinstance(outage, dict) or outage.get("kind") != "bootstrap" or not isinstance(outage.get("samples"), list) or not outage["samples"]:
            errors.append(label + "outage_evidence_missing")

    if len(commitments) == 3 and any(commitments[p] != commitments[10] for p in (50, 100)):
        errors.append("cross_sweep_host_commitments_changed")
    return {
        "schema": "osiris.p2p_capacity_offline_review.v1",
        "passed_offline_consistency": not errors,
        "errors": errors,
        "degraded_loss_sweeps": degraded,
        "exact_commit": expected_commit,
        "workflow_url_claimed": expected_run_url,
        "run_attempt_claimed": expected_attempt,
        "sweeps_checked": list(SWEEPS),
        "external_run_status_verified": False,
        "independent_hosts_externally_attested": False,
        "t04_t19_gate_accepted": False,
        "review_required": True,
        "limitations": [
            "three runner hosts within one private tailnet",
            "30-second workload per sweep",
            "recipient-bootstrap star; open-mesh capacity untested",
            "report source and GitHub conclusion require independent verification",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--write-manifest", type=Path)
    args = parser.parse_args()
    reports: dict[int, object] = {}
    files: list[Path] = []
    for peers in SWEEPS:
        file = args.directory / f"capacity-{peers}.json"
        if file.is_file():
            try:
                reports[peers] = json.loads(file.read_text(encoding="utf-8"))
                files.append(file)
            except (OSError, ValueError):
                reports[peers] = None
        else:
            reports[peers] = None
    result = validate_reports(
        reports, expected_commit=args.commit, expected_run_url=args.run_url,
        expected_attempt=args.attempt,
    )
    if result["passed_offline_consistency"] and args.write_manifest:
        args.write_manifest.write_text(
            "".join(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                for path in sorted(files)
            ),
            encoding="utf-8",
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed_offline_consistency"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
