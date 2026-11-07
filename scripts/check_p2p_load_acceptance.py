#!/usr/bin/env python3
"""Check retained, operator-supplied P2P outage/load evidence fail-closed.

The checked-in policy declares numerical targets before a run. Evidence must
come from an operator-declared real multi-host soak and include exact delivery
accounting, latency percentiles, per-host resources and outage probe samples.
This validates evidence shape and thresholds; it does not independently verify
hosts, measurements, continuous availability or production readiness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from check_p2p_outage_recovery import analyze as analyze_outage

POLICY_PATH = Path(__file__).resolve().parents[1] / "config" / "p2p_load_acceptance_policy.json"
POLICY_SCHEMA = "osiris.p2p_load_acceptance_policy.v1"
EVIDENCE_SCHEMA = "osiris.p2p_load_acceptance.v1"
POLICY_FIELDS = {
    "minimum_peers", "minimum_messages", "minimum_duration_seconds",
    "maximum_recovery_ms", "maximum_latency_p95_ms", "maximum_latency_p99_ms",
    "maximum_lost_messages", "maximum_rejected_messages",
    "maximum_pending_messages", "maximum_rss_bytes_per_host",
    "maximum_cpu_percent_per_host", "maximum_disk_growth_bytes_per_host",
}


def _number(value: object, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    value = float(value)
    if not math.isfinite(value) or value < 0 or (positive and value <= 0):
        qualifier = "positive" if positive else "nonnegative"
        raise ValueError(f"{name} must be a finite {qualifier} number")
    return value


def _integer(value: object, name: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < 0 or (positive and value <= 0):
        raise ValueError(f"{name} must be a {'positive' if positive else 'nonnegative'} integer")
    return value


def _time(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_policy(policy: dict) -> None:
    if not isinstance(policy, dict) or policy.get("schema") != POLICY_SCHEMA:
        raise ValueError("invalid load acceptance policy schema")
    if set(policy) != POLICY_FIELDS | {"schema"}:
        raise ValueError("policy must contain exactly the declared limit fields")
    for name in POLICY_FIELDS:
        _number(policy[name], name, positive=name.startswith("minimum_") or name in {
            "maximum_recovery_ms", "maximum_latency_p95_ms", "maximum_latency_p99_ms",
            "maximum_rss_bytes_per_host", "maximum_disk_growth_bytes_per_host",
        })
    for name in ("minimum_peers", "minimum_messages", "maximum_recovery_ms",
                 "maximum_lost_messages", "maximum_rejected_messages", "maximum_pending_messages"):
        positive = name.startswith("minimum_") or name == "maximum_recovery_ms"
        _integer(policy[name], name, positive=positive)
    if policy["minimum_peers"] < 2:
        raise ValueError("minimum_peers must require a multi-peer workload")
    if policy["maximum_latency_p95_ms"] > policy["maximum_latency_p99_ms"]:
        raise ValueError("p95 limit cannot exceed p99 limit")
    if policy["maximum_cpu_percent_per_host"] > 100:
        raise ValueError("CPU limit cannot exceed 100 percent")


def check(data: dict, policy: dict) -> dict:
    validate_policy(policy)
    if not isinstance(data, dict) or data.get("schema") != EVIDENCE_SCHEMA:
        raise ValueError("invalid load acceptance evidence schema")
    if data.get("measurement_class") != "operator_attested_real_multi_host":
        raise ValueError("only operator-attested real multi-host evidence is accepted")
    commit = data.get("commit_sha")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("commit_sha must be one full lowercase Git commit")
    url = data.get("workflow_url")
    parsed_url = urlparse(url) if isinstance(url, str) else None
    if (
        parsed_url is None or len(url) > 2048
        or parsed_url.scheme != "https" or not parsed_url.netloc
    ):
        raise ValueError("workflow_url must be a retained HTTPS run URL")
    _integer(data.get("attempt"), "attempt", positive=True)
    if not isinstance(data.get("operator"), str) or not 1 <= len(data["operator"].strip()) <= 128:
        raise ValueError("operator must be a non-empty bounded identifier")

    started = _time(data.get("started_at"), "started_at")
    ended = _time(data.get("ended_at"), "ended_at")
    elapsed = (ended - started).total_seconds()
    if elapsed <= 0:
        raise ValueError("ended_at must be after started_at")

    workload = data.get("workload")
    if not isinstance(workload, dict):
        raise ValueError("workload must be an object")
    peers = _integer(workload.get("peers"), "workload.peers", positive=True)
    messages = _integer(workload.get("messages"), "workload.messages", positive=True)
    _integer(workload.get("fanout"), "workload.fanout", positive=True)
    duration = _number(workload.get("duration_seconds"), "workload.duration_seconds", positive=True)
    if abs(duration - elapsed) > 1.0:
        raise ValueError("workload duration must match started_at/ended_at within one second")

    delivery = data.get("delivery")
    if not isinstance(delivery, dict):
        raise ValueError("delivery must be an object")
    counts = {
        name: _integer(delivery.get(name), f"delivery.{name}")
        for name in ("sent", "delivered", "lost", "rejected", "pending")
    }
    if counts["sent"] != messages:
        raise ValueError("delivery.sent must equal workload.messages")
    accounted = sum(counts[name] for name in ("delivered", "lost", "rejected", "pending"))
    if accounted != counts["sent"]:
        raise ValueError("delivery outcomes must be mutually exclusive and sum to sent")

    latency = data.get("latency_ms")
    if not isinstance(latency, dict):
        raise ValueError("latency_ms must be an object")
    if _integer(latency.get("count"), "latency_ms.count") != counts["delivered"]:
        raise ValueError("latency_ms.count must equal delivered messages")
    percentiles = {
        name: _number(latency.get(name), f"latency_ms.{name}")
        for name in ("p50", "p95", "p99", "max")
    }
    if not percentiles["p50"] <= percentiles["p95"] <= percentiles["p99"] <= percentiles["max"]:
        raise ValueError("latency percentiles must be ordered")

    hosts = data.get("hosts")
    if not isinstance(hosts, list) or not 2 <= len(hosts) <= 1000:
        raise ValueError("at least two and at most 1000 host records are required")
    host_ids: set[str] = set()
    host_metrics: list[tuple[str, float, float, float]] = []
    for host in hosts:
        if not isinstance(host, dict):
            raise ValueError("each host must be an object")
        host_id = host.get("host_id")
        if not isinstance(host_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", host_id):
            raise ValueError("host_id must be a simple bounded identifier")
        if host_id in host_ids:
            raise ValueError("host_id must be unique")
        host_ids.add(host_id)
        binary = host.get("binary_sha256")
        if not isinstance(binary, str) or not re.fullmatch(r"[0-9a-f]{64}", binary):
            raise ValueError("each host requires a full lowercase binary_sha256")
        rss = _number(host.get("rss_peak_bytes"), "rss_peak_bytes", positive=True)
        cpu = _number(host.get("cpu_percent"), "cpu_percent")
        disk = _number(host.get("disk_growth_bytes"), "disk_growth_bytes")
        if cpu > 100:
            raise ValueError("cpu_percent cannot exceed 100")
        host_metrics.append((host_id, rss, cpu, disk))

    outage = data.get("outage")
    outage_kinds = {"relay", "bootstrap", "relay_and_bootstrap"}
    if not isinstance(outage, dict) or outage.get("kind") not in outage_kinds:
        raise ValueError("outage.kind must declare relay, bootstrap or relay_and_bootstrap")
    outage_result = analyze_outage(
        outage, outage.get("outage_start_ms"), policy["maximum_recovery_ms"]
    )

    failures: list[str] = []
    if data.get("clean_worktree") is not True:
        failures.append("clean_worktree_required")
    for field, actual, limit in (
        ("minimum_peers", peers, policy["minimum_peers"]),
        ("minimum_messages", messages, policy["minimum_messages"]),
        ("minimum_duration_seconds", duration, policy["minimum_duration_seconds"]),
    ):
        if actual < limit:
            failures.append(field)
    for field, actual, limit in (
        ("maximum_latency_p95_ms", percentiles["p95"], policy["maximum_latency_p95_ms"]),
        ("maximum_latency_p99_ms", percentiles["p99"], policy["maximum_latency_p99_ms"]),
        ("maximum_lost_messages", counts["lost"], policy["maximum_lost_messages"]),
        ("maximum_rejected_messages", counts["rejected"], policy["maximum_rejected_messages"]),
        ("maximum_pending_messages", counts["pending"], policy["maximum_pending_messages"]),
    ):
        if actual > limit:
            failures.append(field)
    for host_id, rss, cpu, disk in host_metrics:
        for field, actual, limit in (
            ("maximum_rss_bytes_per_host", rss, policy["maximum_rss_bytes_per_host"]),
            ("maximum_cpu_percent_per_host", cpu, policy["maximum_cpu_percent_per_host"]),
            (
                "maximum_disk_growth_bytes_per_host", disk,
                policy["maximum_disk_growth_bytes_per_host"],
            ),
        ):
            if actual > limit:
                failures.append(f"{field}:{host_id}")
    if outage_result["status"] != "pass":
        failures.append("maximum_recovery_ms")

    policy_digest = hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "status": "pass" if not failures else "fail",
        "gate": "operator_attested_outage_load_acceptance",
        "commit_sha": commit,
        "policy_sha256": policy_digest,
        "failures": failures,
        "declared_workload": {"peers": peers, "messages": messages, "duration_seconds": duration},
        "operator_declared_host_count": len(hosts),
        "sampled_recovery_ms": outage_result["sampled_recovery_ms"],
        "independent_hosts_verified": False,
        "continuous_availability_verified": False,
        "production_readiness_evidence": False,
        "capacity_claim": "declared_workload_only",
        "evidence_boundary": "operator_attested_metrics_not_independently_verified",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    args = parser.parse_args(argv)
    try:
        policy = json.loads(args.policy.read_text(encoding="utf-8"))
        data = json.loads(args.evidence.read_text(encoding="utf-8"))
        result = check(data, policy)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        result = {
            "status": "fail", "gate": "operator_attested_outage_load_acceptance",
            "reason": str(exc),
        }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
