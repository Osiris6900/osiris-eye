#!/usr/bin/env python3
"""Integrate real local Rust health checks with audit-chain *advisory* evidence.

No invented per-peer metrics. No network mutation. The returned audit chain is
in-memory and needs an independent trusted tip and durable storage to persist.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time

from p2p_decision_audit_chain import append, verify, canonical
from p2p_health_intelligence_bridge import evaluate_snapshots, read_loopback_health


def run_pipeline(snapshots: list[object], observed_at_ms: int) -> dict:
    health = evaluate_snapshots(snapshots)
    if health.get("status") != "observed_local_health":
        return {"status": "rejected", "reason": health.get("reason", "invalid_health"),
                "network_mutated": False, "live_trading_authorized": False}
    if type(observed_at_ms) is not int or observed_at_ms < 0:
        return {"status": "rejected", "reason": "invalid_observed_at",
                "network_mutated": False, "live_trading_authorized": False}
    chain = []
    for report in sorted(health["nodes"], key=lambda x: x["peer_id"]):
        evidence = {
            "peer_id": report["peer_id"],
            "network_brain": report["network_brain"],
            "anomaly_detection": report["anomaly_detection"],
        }
        evidence_digest = hashlib.sha256(canonical(evidence)).hexdigest()
        decision = "investigate" if report["anomaly_detection"] else "observe"
        chain = append(chain, {
            "decision_id": f"health:{observed_at_ms}:{report['peer_id']}",
            "peer_id": report["peer_id"],
            "observed_at_ms": observed_at_ms,
            "decision": decision,
            "evidence_sha256": evidence_digest,
        })
    return {"status": "advisory_only", "health": health, "audit_chain": chain,
            "audit_verification": verify(chain), "network_mutated": False,
            "durable_audit_storage": False, "independent_hosts_verified": False,
            "live_trading_authorized": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", action="append", required=True,
                        help="Numeric loopback Rust /health endpoint")
    args = parser.parse_args()
    try:
        snapshots = [read_loopback_health(url) for url in args.endpoint]
        result = run_pipeline(snapshots, int(time.time() * 1000))
    except (OSError, ValueError, TypeError) as exc:
        result = {"status": "rejected", "reason": str(exc),
                  "network_mutated": False, "live_trading_authorized": False}
    print(json.dumps(result, sort_keys=True))
    return int(result["status"] == "rejected")


if __name__ == "__main__":
    raise SystemExit(main())
