#!/usr/bin/env python3
"""Offline keyless-*API* Ghost trade consistency checker.

No API token is needed. Node identity keys and authenticated transport remain
mandatory. Agreement between sources is not proof of market truth.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path


def check(payload: object, *, now_ms: int, max_age_ms: int = 5000,
          tolerance_bps: int = 25) -> dict:
    errors = []
    if not isinstance(payload, dict) or set(payload) != {"schema", "symbol", "observations"}:
        return {"status": "rejected", "errors": ["invalid_envelope"],
                "provider_truth_verified": False, "live_trading_authorized": False}
    if payload["schema"] != "osiris.ghost-public-trade-consistency.v1":
        errors.append("invalid_schema")
    if not isinstance(payload["symbol"], str) or not payload["symbol"].strip():
        errors.append("invalid_symbol")
    if type(now_ms) is not int or now_ms < 0 or type(max_age_ms) is not int or not 1 <= max_age_ms <= 60000:
        errors.append("invalid_time_policy")
    if type(tolerance_bps) is not int or not 1 <= tolerance_bps <= 1000:
        errors.append("invalid_price_policy")
    observations = payload["observations"]
    prices = []
    sources = set()
    if not isinstance(observations, list) or not 2 <= len(observations) <= 20:
        errors.append("invalid_observation_count")
    else:
        for obs in observations:
            if not isinstance(obs, dict) or set(obs) != {
                "source", "symbol", "price", "exchange_ms", "received_ms", "transport_authenticated"
            }:
                errors.append("invalid_observation")
                continue
            source = obs["source"]
            if not isinstance(source, str) or not source.strip() or source in sources:
                errors.append("duplicate_or_invalid_source")
            else:
                sources.add(source)
            if obs["symbol"] != payload["symbol"]:
                errors.append("symbol_mismatch")
            try:
                price = Decimal(obs["price"])
                if not price.is_finite() or price <= 0:
                    raise ValueError()
                prices.append(price)
            except (InvalidOperation, ValueError, TypeError):
                errors.append("invalid_price")
            exchange_ms, received_ms = obs["exchange_ms"], obs["received_ms"]
            if type(exchange_ms) is not int or type(received_ms) is not int:
                errors.append("invalid_timestamp")
            elif not (0 <= exchange_ms <= received_ms <= now_ms + 1000) or now_ms - received_ms > max_age_ms:
                errors.append("stale_future_or_disordered_timestamp")
            if obs["transport_authenticated"] is not True:
                errors.append("unauthenticated_transport")
    if not errors and prices:
        ordered = sorted(prices)
        mid = ordered[len(ordered) // 2]
        if any(abs(price - mid) * 10000 > mid * tolerance_bps for price in prices):
            errors.append("price_divergence")
    return {
        "status": "consistent" if not errors else "rejected",
        "errors": sorted(set(errors)),
        "distinct_reported_sources": len(sources),
        "provider_truth_verified": False,
        "source_independence_verified": False,
        "node_identity_keyless": False,
        "api_credentials_required": False,
        "live_trading_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--now-ms", type=int, required=True)
    args = parser.parse_args()
    try:
        result = check(json.loads(args.fixture.read_text(encoding="utf-8")), now_ms=args.now_ms)
    except (OSError, ValueError) as exc:
        result = {"status": "rejected", "errors": [str(exc)],
                  "live_trading_authorized": False}
    print(json.dumps(result, sort_keys=True))
    return int(result["status"] != "consistent")


if __name__ == "__main__":
    raise SystemExit(main())
