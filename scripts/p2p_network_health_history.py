"""Bounded network health history: offline failure detection, not peer provenance.

Input samples are timestamped reports produced by evaluate_snapshots().
All output is advisory. Independent-host authentication is not inferred.
"""
from __future__ import annotations


def evaluate_health_history(samples: object) -> dict:
    reject = lambda reason: {"status": "rejected", "reason": reason,
                             "network_mutated": False, "live_trading_authorized": False}
    if not isinstance(samples, list) or not 2 <= len(samples) <= 100:
        return reject("invalid_sample_count")
    previous_time = -1
    previous_peers = None
    alerts = []
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict) or set(sample) != {"observed_at_ms", "health"}:
            return reject("invalid_sample_shape")
        ts, health = sample["observed_at_ms"], sample["health"]
        if type(ts) is not int or ts <= previous_time or ts < 0:
            return reject("nonmonotonic_sample_time")
        previous_time = ts
        if not isinstance(health, dict) or health.get("status") != "observed_local_health":
            return reject("unverified_health_report")
        nodes = health.get("nodes")
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 20:
            return reject("invalid_node_count")
        peers = {}
        for node in nodes:
            if not isinstance(node, dict) or not isinstance(node.get("peer_id"), str):
                return reject("invalid_node")
            peer = node["peer_id"]
            brain = node.get("network_brain")
            flags = node.get("anomaly_detection")
            if (not peer.strip() or peer in peers or not isinstance(brain, dict)
                    or type(brain.get("connected_peers")) is not int
                    or brain["connected_peers"] < 0 or not isinstance(flags, list)
                    or len(flags) > 3
                    or any(flag not in ("isolated_node", "all_bootstraps_disconnected",
                                        "partial_bootstrap_disconnect") for flag in flags)):
                return reject("invalid_node_metrics")
            if len(set(flags)) != len(flags):
                return reject("invalid_node_metrics")
            peers[peer] = brain["connected_peers"]
            for flag in flags:
                alerts.append({"sample": index, "peer_id": peer, "kind": flag})
        if previous_peers is not None:
            for peer in sorted(previous_peers.keys() - peers.keys()):
                alerts.append({"sample": index, "peer_id": peer, "kind": "node_missing"})
            for peer in sorted(previous_peers.keys() & peers.keys()):
                if previous_peers[peer] > 0 and peers[peer] == 0:
                    alerts.append({"sample": index, "peer_id": peer, "kind": "connectivity_lost"})
        previous_peers = peers
    return {"status": "advisory_only", "sample_count": len(samples),
            "alerts": alerts, "authenticated_peers_verified": False,
            "remote_delivery_verified": False, "network_mutated": False,
            "live_trading_authorized": False}
