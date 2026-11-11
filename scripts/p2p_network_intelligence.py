#!/usr/bin/env python3
"""Offline, deterministic P2P network-intelligence advisory.

Never changes libp2p routes, admission policies, identities or live execution.
Metrics are caller claims, not authenticated measurements.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def advise(payload: object) -> dict:
    errors = []
    if not isinstance(payload, dict) or set(payload) != {"schema", "peers"}:
        return _result([], ["invalid_envelope"])
    if payload["schema"] != "osiris.p2p_network_intelligence.v1":
        errors.append("invalid_schema")
    peers = payload["peers"]
    if not isinstance(peers, list) or not 1 <= len(peers) <= 100:
        return _result([], errors + ["invalid_peer_count"])
    seen = set()
    candidates = []
    for peer in peers:
        if not isinstance(peer, dict) or set(peer) != {
            "peer_id", "latency_ms", "delivery_success_bps", "disconnects_10m",
            "duplicate_messages_10m", "authenticated"
        }:
            errors.append("invalid_peer_record")
            continue
        peer_id = peer["peer_id"]
        if not isinstance(peer_id, str) or not peer_id.strip() or peer_id in seen:
            errors.append("duplicate_or_invalid_peer_id")
            continue
        seen.add(peer_id)
        metrics = ("latency_ms", "delivery_success_bps", "disconnects_10m", "duplicate_messages_10m")
        if any(type(peer[key]) is not int or peer[key] < 0 for key in metrics):
            errors.append("invalid_metric")
            continue
        if peer["delivery_success_bps"] > 10000 or peer["latency_ms"] > 600000:
            errors.append("metric_out_of_bounds")
            continue
        if peer["authenticated"] is not True:
            errors.append("unauthenticated_peer")
            continue
        anomalies = []
        if peer["delivery_success_bps"] < 9500:
            anomalies.append("low_delivery_success")
        if peer["disconnects_10m"] >= 5:
            anomalies.append("frequent_disconnects")
        if peer["duplicate_messages_10m"] >= 100:
            anomalies.append("high_duplicate_rate")
        if peer["latency_ms"] >= 2000:
            anomalies.append("high_latency")
        # Integer-only deterministic ordering, not a calibrated route utility.
        score = (peer["latency_ms"] + 20 * peer["disconnects_10m"]
                 + 2 * peer["duplicate_messages_10m"]
                 + (10000 - peer["delivery_success_bps"]) // 10)
        candidates.append({"peer_id": peer_id, "advisory_cost": score,
                           "anomalies": anomalies, "candidate_healthy": not anomalies})
    if errors:
        return _result([], errors)
    candidates.sort(key=lambda item: (not item["candidate_healthy"], item["advisory_cost"], item["peer_id"]))
    return _result(candidates, [])


def _result(candidates: list, errors: list) -> dict:
    return {
        "status": "advisory_only" if not errors else "rejected",
        "errors": sorted(set(errors)),
        "ranked_peers": candidates,
        "automatic_routing_enabled": False,
        "peer_metrics_independently_verified": False,
        "network_anomaly_proven": False,
        "live_trading_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    try:
        output = advise(json.loads(args.snapshot.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        output = _result([], ["cannot_read_snapshot:" + str(exc)])
    print(json.dumps(output, sort_keys=True))
    return int(output["status"] == "rejected")


if __name__ == "__main__":
    raise SystemExit(main())
