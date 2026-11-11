"""Five offline P2P intelligence advisories. No network changes."""
import json
import sys

def evaluate(data):
    def reject(reason):
        return {"status": "rejected", "reason": reason, "network_mutated": False}
    if not isinstance(data, dict) or data.get("schema") != "osiris.p2p.intelligence.v1" or set(data) != {"schema", "peers"}:
        return reject("invalid_envelope")
    peers = data["peers"]
    if not isinstance(peers, list) or not 1 <= len(peers) <= 100:
        return reject("invalid_peer_count")
    seen, results = set(), []
    for p in peers:
        required = {"peer_id", "authenticated", "samples", "latency_ms", "delivery_bps", "previous_delivery_bps", "disconnects_10m", "duplicates_10m", "cpu_percent", "queue_depth"}
        if not isinstance(p, dict) or set(p) != required:
            return reject("invalid_peer_record")
        if not isinstance(p["peer_id"], str) or not p["peer_id"].strip() or p["peer_id"] in seen:
            return reject("invalid_peer_identity")
        seen.add(p["peer_id"])
        if p["authenticated"] is not True:
            return reject("unauthenticated_peer")
        numeric = required - {"peer_id", "authenticated"}
        if any(type(p[k]) is not int or p[k] < 0 for k in numeric):
            return reject("invalid_metric")
        if p["samples"] < 20 or p["delivery_bps"] > 10000 or p["previous_delivery_bps"] > 10000 or p["cpu_percent"] > 100 or p["latency_ms"] > 600000:
            return reject("metric_out_of_bounds_or_insufficient_samples")
        anomalies = []
        if p["latency_ms"] >= 2000: anomalies.append("high_latency")
        if p["delivery_bps"] < 9500: anomalies.append("low_delivery")
        if p["disconnects_10m"] >= 5: anomalies.append("repeated_disconnect")
        if p["duplicates_10m"] >= 100: anomalies.append("duplicate_spike")
        warning = p["previous_delivery_bps"] - p["delivery_bps"] >= 500 or p["disconnects_10m"] >= 5 or p["queue_depth"] >= 1000
        cost = p["latency_ms"] + (10000-p["delivery_bps"])//10 + 20*p["disconnects_10m"] + 2*p["duplicates_10m"]
        suggestions = []
        if p["cpu_percent"] >= 85: suggestions.append("investigate_cpu")
        if p["queue_depth"] >= 1000: suggestions.append("investigate_backpressure")
        if anomalies: suggestions.append("investigate_peer_health")
        results.append({"peer_id": p["peer_id"],
            "network_brain": {"health": "degraded" if anomalies else "nominal", "samples": p["samples"]},
            "smart_routing": {"advisory_cost": cost, "preferred": not anomalies and not warning},
            "anomaly_detection": anomalies,
            "predictive_failure": {"early_warning": warning, "validated_probability": None},
            "autonomous_optimization": {"suggestions": suggestions, "applied": False}})
    results.sort(key=lambda x: (not x["smart_routing"]["preferred"], x["smart_routing"]["advisory_cost"], x["peer_id"]))
    return {"status": "advisory_only", "ranked_peers": results, "network_mutated": False, "metrics_independently_verified": False, "live_trading_authorized": False}

if __name__ == "__main__":
    try:
        result = evaluate(json.load(open(sys.argv[1], encoding="utf-8")))
    except (OSError, ValueError, IndexError) as exc:
        result = {"status": "rejected", "reason": str(exc), "network_mutated": False}
    print(json.dumps(result, sort_keys=True))
    sys.exit(result["status"] == "rejected")
