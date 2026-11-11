#!/usr/bin/env python3
"""Fail-closed offline data quality and timestamp skew guard.

This module never claims independent provider authentication, market truth or
live order authorization. Timestamp comparisons use an injected trusted clock.
"""
from __future__ import annotations
import hashlib
import json
import math


def inspect_observations(observations: object, *, now_ms: int,
                         max_age_ms: int = 5000, max_future_skew_ms: int = 1000) -> dict:
    if (type(now_ms) is not int or now_ms < 0 or
            type(max_age_ms) is not int or max_age_ms < 0 or
            type(max_future_skew_ms) is not int or max_future_skew_ms < 0):
        return {"status": "rejected", "reason": "invalid_clock_configuration",
                "live_trading_authorized": False}
    if not isinstance(observations, list) or not 1 <= len(observations) <= 1000:
        return {"status": "rejected", "reason": "invalid_observation_count",
                "live_trading_authorized": False}
    ids = set()
    findings = []
    for index, observation in enumerate(observations):
        required = {"observation_id", "provider", "instrument", "observed_at_ms", "price"}
        if not isinstance(observation, dict) or set(observation) != required:
            return {"status": "rejected", "reason": "invalid_observation_shape",
                    "index": index, "live_trading_authorized": False}
        oid, provider, instrument = (observation[k] for k in
                                     ("observation_id", "provider", "instrument"))
        if any(not isinstance(v, str) or not v.strip() or len(v) > 128
               for v in (oid, provider, instrument)):
            return {"status": "rejected", "reason": "invalid_identity",
                    "index": index, "live_trading_authorized": False}
        timestamp = observation["observed_at_ms"]
        price = observation["price"]
        if (type(timestamp) is not int or timestamp < 0 or
                isinstance(price, bool) or type(price) not in (int, float) or
                not math.isfinite(price) or price <= 0):
            return {"status": "rejected", "reason": "invalid_time_or_price",
                    "index": index, "live_trading_authorized": False}
        flags = []
        if oid in ids:
            flags.append("duplicate_observation_id")
        ids.add(oid)
        if now_ms - timestamp > max_age_ms:
            flags.append("stale_observation")
        if timestamp - now_ms > max_future_skew_ms:
            flags.append("future_clock_skew")
        findings.append({"observation_id": oid, "flags": flags,
                         "accepted_for_research": not flags})
    return {"status": "quality_checked", "findings": findings,
            "all_accepted": all(f["accepted_for_research"] for f in findings),
            "provider_authenticated": False,
            "independent_clock_verified": False,
            "live_trading_authorized": False}
