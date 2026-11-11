#!/usr/bin/env python3
"""Read actual local Rust /health endpoints, report safe P2P readiness facts.

Never trust reported peer counts as independent host identity, successful
observation delivery, authenticated peer provenance or route optimization.
No network changes and no trading.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
from urllib.parse import urlsplit
from urllib.request import urlopen


def evaluate_snapshots(snapshots: list[object]) -> dict:
    if not isinstance(snapshots, list) or not 1 <= len(snapshots) <= 20:
        return {"status": "rejected", "reason": "invalid_snapshot_count"}
    peers = set()
    reports = []
    for node in snapshots:
        fields = {"status", "peer_id", "uptime_seconds", "connected_peers",
                  "bootstrap_peers_connected", "bootstrap_peers_total"}
        if not isinstance(node, dict) or set(node) != fields:
            return {"status": "rejected", "reason": "invalid_health_shape"}
        if node["status"] != "alive" or not isinstance(node["peer_id"], str) or not node["peer_id"].strip() or node["peer_id"] in peers:
            return {"status": "rejected", "reason": "invalid_or_duplicate_peer"}
        peers.add(node["peer_id"])
        metrics = fields - {"status", "peer_id"}
        if any(type(node[k]) is not int or node[k] < 0 for k in metrics):
            return {"status": "rejected", "reason": "invalid_counter"}
        if (node["bootstrap_peers_connected"] > node["bootstrap_peers_total"]
                or node["bootstrap_peers_connected"] > node["connected_peers"]):
            return {"status": "rejected", "reason": "inconsistent_counters"}
        connected = node["bootstrap_peers_connected"]
        total = node["bootstrap_peers_total"]
        flags = []
        if total and connected == 0:
            flags.append("all_bootstraps_disconnected")
        elif total and connected < total:
            flags.append("partial_bootstrap_disconnect")
        if node["connected_peers"] == 0:
            flags.append("isolated_node")
        reports.append({
            "peer_id": node["peer_id"],
            "network_brain": {"connected_peers": node["connected_peers"],
                              "uptime_seconds": node["uptime_seconds"]},
            "anomaly_detection": flags,
            "smart_routing": {"ranking_available": False, "reason": "no_peer_latency_or_delivery_metrics"},
            "predictive_failure": {"prediction_available": False, "reason": "no_time_series"},
            "autonomous_optimization": {"suggestions": ["inspect_bootstrap_connectivity"] if flags else [],
                                         "applied": False},
        })
    return {"status": "observed_local_health", "nodes": reports,
            "peer_authentication_verified": False,
            "remote_delivery_verified": False,
            "network_mutated": False, "live_trading_authorized": False}


def read_loopback_health(url: str) -> dict:
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path != "/health":
        raise ValueError("only_loopback_http_health_allowed")
    if not parsed.hostname:
        raise ValueError("missing_host")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise ValueError("numeric_loopback_address_required") from exc
    if not address.is_loopback or parsed.port is None:
        raise ValueError("numeric_loopback_address_and_port_required")
    # Avoid proxy resolution or DNS-based SSRF; numeric loopback only.
    from urllib.request import build_opener, ProxyHandler
    opener = build_opener(ProxyHandler({}))
    with opener.open(url, timeout=3) as response:
        if response.status != 200:
            raise ValueError("health_http_error")
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError("health_response_too_large")
    return json.loads(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", action="append", required=True,
                        help="Numeric loopback HTTP health endpoint, e.g. http://127.0.0.1:9090/health")
    args = parser.parse_args()
    try:
        result = evaluate_snapshots([read_loopback_health(url) for url in args.endpoint])
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"status": "rejected", "reason": str(exc),
                  "network_mutated": False, "live_trading_authorized": False}
    print(json.dumps(result, sort_keys=True))
    return int(result["status"] == "rejected")


if __name__ == "__main__":
    raise SystemExit(main())
