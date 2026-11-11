#!/usr/bin/env python3
"""Fail-closed offline P2P bootstrap health and recovery state evaluator.

The evaluator is not an active reconnection controller and cannot prove
delivery, public reachability or independent host identity.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def evaluate(sample: object, *, min_bootstrap: int = 1,
             max_recovery_seconds: int = 120) -> dict:
    errors: list[str] = []
    if not isinstance(sample, dict) or set(sample) != {
        "before", "after", "outage_duration_seconds", "recovery_duration_seconds"
    }:
        return {"state": "invalid", "errors": ["invalid_sample_shape"],
                "self_healing_proven": False, "release_accepted": False}
    if type(min_bootstrap) is not int or min_bootstrap < 1:
        errors.append("invalid_min_bootstrap")
    if type(max_recovery_seconds) is not int or max_recovery_seconds < 1:
        errors.append("invalid_recovery_policy")
    for name in ("before", "after"):
        node = sample[name]
        if not isinstance(node, dict) or set(node) != {
            "status", "peer_id", "connected_peers", "bootstrap_peers_connected",
            "bootstrap_peers_total", "uptime_seconds"
        }:
            errors.append(name + "_invalid_health_shape")
            continue
        if node["status"] != "alive" or not isinstance(node["peer_id"], str) or not node["peer_id"].strip():
            errors.append(name + "_invalid_identity_or_liveness")
        counts = ("connected_peers", "bootstrap_peers_connected", "bootstrap_peers_total", "uptime_seconds")
        if any(type(node[k]) is not int or node[k] < 0 for k in counts):
            errors.append(name + "_invalid_counts")
        elif node["bootstrap_peers_connected"] > node["bootstrap_peers_total"] or node["bootstrap_peers_connected"] > node["connected_peers"]:
            errors.append(name + "_inconsistent_counts")
    if not errors:
        before, after = sample["before"], sample["after"]
        if before["peer_id"] != after["peer_id"]:
            errors.append("node_identity_changed")
        if after["bootstrap_peers_total"] < min_bootstrap:
            errors.append("insufficient_configured_bootstraps")
        if before["bootstrap_peers_connected"] >= min_bootstrap:
            errors.append("outage_not_observed")
        if after["bootstrap_peers_connected"] < min_bootstrap:
            errors.append("bootstrap_not_recovered")
        if after["connected_peers"] == 0:
            errors.append("no_connected_peers")
    for name in ("outage_duration_seconds", "recovery_duration_seconds"):
        if type(sample[name]) is not int or sample[name] < 0:
            errors.append("invalid_" + name)
    if not errors and sample["recovery_duration_seconds"] > max_recovery_seconds:
        errors.append("recovery_slo_exceeded")
    return {
        "state": "reported_recovered" if not errors else "rejected",
        "errors": sorted(set(errors)),
        "self_healing_proven": False,
        "remote_delivery_verified": False,
        "independent_host_verified": False,
        "release_accepted": False,
        "boundary": "operator_supplied_health_snapshots_only",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        result = evaluate(json.loads(args.report.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        result = {"state": "invalid", "errors": [str(exc)], "release_accepted": False}
    print(json.dumps(result, sort_keys=True))
    return int(result["state"] != "reported_recovered")


if __name__ == "__main__":
    raise SystemExit(main())
