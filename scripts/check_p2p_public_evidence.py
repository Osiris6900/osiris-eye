#!/usr/bin/env python3
"""Validate independent-vantage public TCP probe evidence.

Collect each probe on a separately administered network, then run:
  python scripts/check_p2p_public_evidence.py --target-host node.example.org \
    --target-port 4001 --max-age-seconds 3600 site-a.json site-b.json

Evidence checks are administrative only: vantage IDs are operator declarations,
not attested network independence. TCP success does NOT verify libp2p, Noise,
E2EE, PeerId identity, relay fallback, or public service readiness.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path


def parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp_utc must be a string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp_utc is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp_utc must include a timezone")
    return parsed.astimezone(timezone.utc)


def check(records: list[dict], host: str, port: int, max_age: int,
          now: datetime | None = None) -> dict:
    if len(records) < 2:
        raise ValueError("at least two evidence records are required")
    if not 1 <= port <= 65535 or not 1 <= max_age <= 86400:
        raise ValueError("invalid target port or maximum age")
    now = now or datetime.now(timezone.utc)
    vantages: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("each record must be a JSON object")
        vantage = record.get("vantage_id")
        if not isinstance(vantage, str) or not 1 <= len(vantage) <= 64 or not all(
            c.isalnum() or c in "-_" for c in vantage
        ):
            raise ValueError("each record requires a simple operator-declared vantage_id")
        if vantage in vantages:
            raise ValueError("vantage_id must be unique")
        vantages.add(vantage)
        if record.get("target_host") != host or type(record.get("target_port")) is not int or record["target_port"] != port:
            raise ValueError("all records must target the exact same host and port")
        if record.get("verification_level") != "tcp_only" or record.get("tcp_reachable") is not True:
            raise ValueError("all records must report a successful TCP-only probe")
        for field in ("authenticated_p2p_verified", "e2ee_verified", "relay_fallback_verified"):
            if record.get(field) is not False:
                raise ValueError("TCP-only evidence must not claim higher-layer verification")
        elapsed = (now - parse_time(record.get("timestamp_utc"))).total_seconds()
        if elapsed < -60 or elapsed > max_age:
            raise ValueError("evidence timestamp is stale or in the future")
    return {
        "status": "pass",
        "gate": "operator_declared_multi_vantage_tcp_only",
        "target_host": host,
        "target_port": port,
        "vantage_count": len(vantages),
        "independent_networks_attested": False,
        "authenticated_p2p_verified": False,
        "e2ee_verified": False,
        "relay_fallback_verified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-port", required=True, type=int)
    parser.add_argument("--max-age-seconds", type=int, default=3600)
    parser.add_argument("evidence", nargs="+", type=Path)
    args = parser.parse_args()
    try:
        records = [json.loads(p.read_text(encoding="utf-8")) for p in args.evidence]
        result = check(records, args.target_host, args.target_port, args.max_age_seconds)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "fail", "gate": "multi_vantage_tcp_only", "reason": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
