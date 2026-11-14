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
import math
import re
from pathlib import Path

from check_p2p_load_acceptance import POLICY_PATH, check

_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RUN = re.compile(r"https://github\.com/Osiris6900/osiris-eye/actions/runs/[1-9][0-9]*\Z")
_HOSTS = {"host-a", "host-b", "host-c"}



def traffic_window_errors(data: dict) -> list[str]:
    """Cross-check traffic-on claims against every actual workload record."""
    errors = []
    notes, outage = data.get("measurement_notes"), data.get("outage")
    records, workload, delivery = data.get("delivery_records"), data.get("workload"), data.get("delivery")
    if not all(isinstance(value, dict) for value in (notes, outage, workload, delivery)):
        return ["traffic_on_sections_missing"]
    if (notes.get("traffic_mode") != "continuous_during_outage"
            or notes.get("workload_drained_before_outage") is not False
            or notes.get("workload_retransmissions") != 0
            or type(notes.get("workload_retransmissions")) is not int):
        errors.append("traffic_on_mode_or_no_resend_claim_invalid")
    boundaries = [outage.get(field) for field in (
        "outage_start_ms", "sink_restart_ms", "all_publishers_recovered_ms")]
    if (any(type(value) is not int or value < 0 for value in boundaries)
            or not boundaries[0] < boundaries[1] <= boundaries[2]):
        return errors + ["traffic_on_outage_window_invalid"]
    start, restart, recovered = boundaries
    duration = workload.get("duration_seconds")
    if (not isinstance(duration, (int, float)) or isinstance(duration, bool)
            or not math.isfinite(duration) or not 0 < duration <= 86400):
        return errors + ["traffic_on_observation_duration_invalid"]
    cutoff = math.ceil(duration * 1000) + 2
    if recovered > cutoff:
        errors.append("traffic_on_recovery_after_observation_cutoff")
    if not isinstance(records, list) or len(records) != 10000:
        return errors + ["traffic_on_exact_workload_records_required"]
    seen, down_count, outage_count, delivered_count, lost_count, duplicates, late = set(), 0, 0, 0, 0, 0, 0
    latencies, recovered_publications = [], 0
    for item in records:
        if not isinstance(item, dict):
            errors.append("traffic_on_record_invalid")
            break
        digest, published = item.get("payload_sha256"), item.get("published_elapsed_ms")
        if (not isinstance(digest, str) or not _SHA256.fullmatch(digest) or digest in seen
                or not isinstance(item.get("source_host"), str) or item["source_host"] not in _HOSTS
                or item.get("recipient_host") != "host-b"
                or type(published) is not int or not 0 <= published <= cutoff):
            errors.append("traffic_on_record_identity_or_time_invalid")
            break
        seen.add(digest)
        during, down = start <= published < recovered, start <= published < restart
        if item.get("during_outage") is not during or item.get("while_sink_stopped") is not down:
            errors.append("traffic_on_record_window_mismatch")
            break
        outage_count += int(during)
        down_count += int(down)
        count, duplicate = item.get("delivery_count"), item.get("duplicate_deliveries")
        if (type(count) is not int or count < 0 or type(duplicate) is not int
                or duplicate != max(count - 1, 0)):
            errors.append("traffic_on_duplicate_accounting_invalid")
            break
        duplicates += duplicate
        received = item.get("delivered_elapsed_ms")
        after_restart = False
        if item.get("outcome") == "delivered":
            if (count < 1 or type(received) is not int or not published <= received <= cutoff
                    or not isinstance(item.get("latency_ms"), (int, float))
                    or isinstance(item.get("latency_ms"), bool)
                    or not 0 <= item["latency_ms"] < float("inf")):
                errors.append("traffic_on_delivery_invalid")
                break
            if abs((received - published) - item["latency_ms"]) > 1.001:
                errors.append("traffic_on_latency_time_mismatch")
                break
            delivered_count += 1
            latencies.append(item["latency_ms"])
            after_restart = received >= restart
        elif item.get("outcome") == "lost":
            if count != 0 or received is not None or item.get("latency_ms") is not None:
                errors.append("traffic_on_loss_invalid")
                break
            lost_count += 1
        else:
            errors.append("traffic_on_outcome_invalid")
            break
        if item.get("delivered_after_restart") is not after_restart:
            errors.append("traffic_on_late_delivery_mismatch")
            break
        late += int(after_restart)
        recovered_publication = during and after_restart
        if item.get("outage_publication_delivered_after_restart") is not recovered_publication:
            errors.append("traffic_on_outage_publication_delivery_mismatch")
            break
        recovered_publications += int(recovered_publication)
    if not down_count:
        errors.append("traffic_on_no_workload_while_sink_stopped")
    latency_summary = data.get("latency_ms")
    ordered = sorted(latencies)
    expected = {name: (ordered[max(0, math.ceil(len(ordered) * q) - 1)] if ordered else 0)
                for name, q in (("p50", .5), ("p95", .95), ("p99", .99), ("max", 1))}
    if not isinstance(latency_summary, dict) or any(
        latency_summary.get(name) != value for name, value in expected.items()
    ):
        errors.append("traffic_on_latency_summary_mismatch")
    if (workload.get("messages_while_sink_stopped") != down_count
            or type(workload.get("messages_while_sink_stopped")) is not int
            or workload.get("messages_during_outage") != outage_count
            or type(workload.get("messages_during_outage")) is not int):
        errors.append("traffic_on_publication_counts_mismatch")
    if (delivery.get("sent") != len(records) or delivery.get("delivered") != delivered_count
            or delivery.get("lost") != lost_count or delivery.get("duplicate_deliveries") != duplicates
            or type(delivery.get("duplicate_deliveries")) is not int
            or delivery.get("delivered_after_restart") != late
            or type(delivery.get("delivered_after_restart")) is not int
            or delivery.get("outage_publications_delivered_after_restart") != recovered_publications
            or type(delivery.get("outage_publications_delivered_after_restart")) is not int):
        errors.append("traffic_on_delivery_totals_mismatch")
    return errors


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
    if "runtime_config" in data:
        runtime = data["runtime_config"]
        if not isinstance(runtime, dict):
            errors.append("runtime_configuration_invalid")
        else:
            workers = runtime.get("node_worker_threads_requested")
            if ((workers is not None and (type(workers) is not int or workers not in (1, 2, 4)))
                    or runtime.get("tokio_worker_threads_env") != (None if workers is None else str(workers))
                    or runtime.get("observed_runtime_worker_count_verified") is not False):
                errors.append("runtime_configuration_invalid")
            if not isinstance(hosts, list) or any(
                not isinstance(host, dict) or host.get("runtime_config") != runtime
                or type(host["runtime_config"].get("node_worker_threads_requested")) is not type(workers)
                or host["runtime_config"].get("observed_runtime_worker_count_verified") is not False
                for host in hosts
            ):
                errors.append("host_runtime_configuration_mismatch")
    notes = data.get("measurement_notes")
    traffic_on = isinstance(notes, dict) and notes.get("traffic_during_outage") is True
    if not isinstance(notes, dict) or (
        notes.get("open_mesh_capacity_tested") is not False
        or notes.get("workload_paused_during_outage") is not (not traffic_on)
        or notes.get("independent_host_fingerprints_distinct") is not True
        or ("traffic_during_outage" in notes and type(notes["traffic_during_outage"]) is not bool)
        or ("traffic_mode" in notes and notes["traffic_mode"] != (
            "continuous_during_outage" if traffic_on else "drained_restart"))
    ):
        errors.append("declared_star_and_outage_limits_missing")
    if traffic_on:
        errors.extend(traffic_window_errors(data))

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
        "declared_traffic_mode": "continuous_during_outage" if traffic_on else "drained_restart",
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
