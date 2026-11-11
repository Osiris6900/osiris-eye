#!/usr/bin/env python3
"""Five-part deterministic P2P intelligence research prototype.

Advisory only. Samples are operator supplied; no cryptographic source validation,
network mutations, or live trading. Historical trends are risk flags, not forecasts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def analyze(payload: object) -> dict:
    base = {"status": "rejected", "errors": [], "network_brain": [],
            "smart_routing": [], "anomaly_detection": [],
            "predictive_failure": [], "optimization": [],
            "automatic_changes_applied": False, "live_trading_authorized": False,
            "metrics_independently_verified": False}
    if not isinstance(payload, dict) or set(payload) != {"schema", "nodes"} or payload.get("schema") != "osiris.p2p_intelligence.v1":
        return {**base, "errors": ["invalid_envelope"]}
    nodes = payload["nodes"]
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 100:
        return {**base, "errors": ["invalid_node_count"]}
    ids = set()
    evaluated = []
    for node in nodes:
        if not isinstance(node, dict) or set(node) != {"peer_id", "authenticated", "samples"}:
            return {**base, "errors": ["invalid_node_record"]}
        identity = node["peer_id"]
        if not isinstance(identity, str) or not identity.strip() or identity in ids:
            return {**base, "errors": ["duplicate_or_invalid_identity"]}
        ids.add(identity)
        if node["authenticated"] is not True:
            return {**base, "errors": ["unauthenticated_node"]}
        samples = node["samples"]
        if not isinstance(samples, list) or not 2 <= len(samples) <= 12:
            return {**base, "errors": ["invalid_sample_count"]}
        previous = -1
        for sample in samples:
            if not isinstance(sample, dict) or set(sample) != {
                "at_ms", "latency_ms", "delivery_bps", "disconnects", "duplicates"
            }:
                return {**base, "errors": ["invalid_sample_shape"]}
            if any(type(v) is not int or v < 0 for v in sample.values()):
                return {**base, "errors": ["invalid_sample_metric"]}
            if sample["at_ms"] <= previous or sample["delivery_bps"] > 10000 or sample["latency_ms"] > 600000:
                return {**base, "errors": ["invalid_sample_range_or_order"]}
            previous = sample["at_ms"]
        latest, earlier = samples[-1], samples[-2]
        anomalies = []
        if latest["latency_ms"] >= 2000:
            anomalies.append("high_latency")
        if latest["delivery_bps"] < 9500:
            anomalies.append("low_delivery")
        if latest["disconnects"] >= 5:
            anomalies.append("frequent_disconnects")
        if latest["duplicates"] >= 100:
            anomalies.append("high_duplicates")
        trends = []
        if latest["latency_ms"] >= max(200, earlier["latency_ms"] * 2):
            trends.append("latency_doubled")
        if earlier["delivery_bps"] - latest["delivery_bps"] >= 500:
            trends.append("delivery_dropped_500bps")
        if latest["disconnects"] - earlier["disconnects"] >= 3:
            trends.append("disconnect_spike")
        score = (latest["latency_ms"] + 20 * latest["disconnects"]
                 + 2 * latest["duplicates"] + (10000 - latest["delivery_bps"]) // 10)
        evaluated.append({"peer_id": identity, "advisory_cost": score,
                          "anomalies": anomalies, "trend_flags": trends,
                          "healthy": not anomalies and not trends,
                          "latency_ms": latest["latency_ms"],
                          "delivery_bps": latest["delivery_bps"]})
    ordered = sorted(evaluated, key=lambda n: (not n["healthy"], n["advisory_cost"], n["peer_id"]))
    actions = []
    for n in ordered:
        if n["anomalies"] or n["trend_flags"]:
            actions.append({"peer_id": n["peer_id"],
                            "action": "investigate_and_measure_before_rerouting",
                            "requires_operator_approval": True})
    return {
        **base, "status": "advisory_only",
        "network_brain": [{"peer_id": n["peer_id"], "latency_ms": n["latency_ms"],
                           "delivery_bps": n["delivery_bps"], "healthy": n["healthy"]}
                          for n in ordered],
        "smart_routing": [{"peer_id": n["peer_id"], "advisory_cost": n["advisory_cost"]}
                          for n in ordered],
        "anomaly_detection": [{"peer_id": n["peer_id"], "flags": n["anomalies"]}
                              for n in ordered if n["anomalies"]],
        "predictive_failure": [{"peer_id": n["peer_id"], "trend_flags": n["trend_flags"],
                                "prediction_validated": False}
                               for n in ordered if n["trend_flags"]],
        "optimization": actions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    try:
        result = analyze(json.loads(args.snapshot.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        result = {"status": "rejected", "errors": [str(exc)],
                  "automatic_changes_applied": False}
    print(json.dumps(result, sort_keys=True))
    return int(result["status"] == "rejected")


if __name__ == "__main__":
    raise SystemExit(main())
