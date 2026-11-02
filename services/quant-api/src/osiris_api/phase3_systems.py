"""Phase 3 institutional workflow foundations. Research and simulation only."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from statistics import mean, pstdev
from typing import Any


def _finite(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def connector_contract(payload: dict[str, Any]) -> dict[str, Any]:
    required = ("provider", "dataset", "timestamp", "license_status")
    missing = [key for key in required if not payload.get(key)]
    licensed = payload.get("license_status") == "authorized"
    return {"accepted": not missing and licensed, "missing_fields": missing,
            "license_authorized": licensed, "adapter_connected": False,
            "new_risk_allowed": False,
            "reason": "contract_only_no_vendor_connection"}


def market_replay(events: list[dict[str, Any]]) -> dict[str, Any]:
    parsed = []
    for index, event in enumerate(events):
        stamp = datetime.fromisoformat(str(event["timestamp"]).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("replay timestamps must include timezone")
        parsed.append((stamp, index, event))
    ordered = sorted(parsed, key=lambda row: (row[0], row[1]))
    return {"event_count": len(ordered), "events": [row[2] for row in ordered],
            "chronological": True, "replay_mode": "deterministic_event_order",
            "execution_or_market_data_connection": False}


def portfolio_optimizer(volatilities: list[float], max_weight: float = 1.0) -> dict[str, Any]:
    if not volatilities or not 0 < max_weight <= 1:
        raise ValueError("volatilities required and max_weight must be in (0, 1]")
    vols = [_finite(v, "volatility") for v in volatilities]
    if any(v <= 0 for v in vols) or len(vols) * max_weight < 1 - 1e-12:
        raise ValueError("volatilities must be positive and cap must permit full investment")
    raw = [1 / v for v in vols]
    weights = [0.0] * len(raw)
    remaining = set(range(len(raw)))
    budget = 1.0
    while remaining:
        denom = sum(raw[i] for i in remaining)
        capped = [i for i in remaining if budget * raw[i] / denom > max_weight]
        if not capped:
            for i in remaining:
                weights[i] = budget * raw[i] / denom
            break
        for i in capped:
            weights[i] = max_weight
            budget -= max_weight
            remaining.remove(i)
    return {"weights": weights, "weight_sum": sum(weights),
            "method": "capped_inverse_volatility_baseline",
            "optimality_claim": False}


def paper_order_lifecycle(order: dict[str, Any]) -> dict[str, Any]:
    qty = _finite(order.get("quantity", 0), "quantity")
    filled = _finite(order.get("filled_quantity", 0), "filled_quantity")
    if qty <= 0 or filled < 0 or filled > qty:
        raise ValueError("invalid order or fill quantity")
    status = "filled" if filled == qty else ("partially_filled" if filled else "accepted_simulation")
    return {"order_id": str(order.get("order_id", "SIM-UNASSIGNED")),
            "status": status, "remaining_quantity": qty - filled,
            "live_order": False, "broker_connected": False}


def factor_diagnostics(asset_returns: dict[str, list[float]],
                       factor_returns: dict[str, list[float]]) -> dict[str, Any]:
    results = {}
    for name, factor in factor_returns.items():
        f = [_finite(x, "factor return") for x in factor]
        if len(f) < 2 or pstdev(f) == 0:
            raise ValueError("each factor needs at least two non-constant observations")
        exposures = {}
        for asset, values in asset_returns.items():
            a = [_finite(x, "asset return") for x in values]
            if len(a) != len(f):
                raise ValueError("asset and factor series must be aligned")
            fm, am = mean(f), mean(a)
            exposures[asset] = sum((x-fm)*(y-am) for x,y in zip(f,a)) / sum((x-fm)**2 for x in f)
        results[name] = exposures
    return {"factor_exposures": results, "method": "historical_OLS_single_factor",
            "causal_interpretation": False, "point_in_time_validation": False}


def derivatives_risk(positions: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {key: 0.0 for key in ("delta", "gamma", "vega", "theta")}
    for p in positions:
        qty = _finite(p.get("quantity", 0), "quantity")
        multiplier = _finite(p.get("multiplier", 1), "multiplier")
        for key in totals:
            totals[key] += _finite(p.get(key, 0), key) * qty * multiplier
    return {"aggregate_greeks": totals, "position_count": len(positions),
            "valuation_model": "not_supplied", "live_options_data": False}


def event_calendar_audit(events: list[dict[str, Any]]) -> dict[str, Any]:
    issues = []
    for i, event in enumerate(events):
        for key in ("event_id", "event_time", "source", "event_type"):
            if not event.get(key):
                issues.append({"index": i, "code": "missing_" + key})
        if event.get("event_time"):
            dt = datetime.fromisoformat(str(event["event_time"]).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                issues.append({"index": i, "code": "timezone_missing"})
    return {"event_count": len(events), "issues": issues,
            "calendar_feed_connected": False, "event_trade_recommendation": False}


def experiment_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    required = ("dataset_version", "code_version", "parameters", "seed", "as_of")
    missing = [key for key in required if key not in payload]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {"reproducible_manifest": not missing, "missing_fields": missing,
            "manifest_sha256": sha256(canonical.encode()).hexdigest(),
            "immutable_registry_write": False}


def portfolio_stress(returns: dict[str, list[float]], weights: dict[str, float],
                     shocks: dict[str, float]) -> dict[str, Any]:
    if set(weights) != set(returns):
        raise ValueError("weights and return assets must match")
    if abs(sum(weights.values()) - 1) > 1e-6:
        raise ValueError("weights must sum to one")
    contributions = {}
    for asset, series in returns.items():
        if not series:
            raise ValueError("stress return series cannot be empty")
        contributions[asset] = weights[asset] * _finite(shocks.get(asset, 0), "shock")
    return {"asset_contributions": contributions,
            "portfolio_shock_return": sum(contributions.values()),
            "scenario_type": "user_supplied_deterministic_shock",
            "probability_estimated": False}


def sre_health(services: list[dict[str, Any]]) -> dict[str, Any]:
    if not services:
        raise ValueError("at least one service check is required")
    unhealthy = [s.get("service", "unknown") for s in services
                 if s.get("status") != "healthy"]
    return {"healthy": not unhealthy, "unhealthy_services": unhealthy,
            "new_risk_allowed": not unhealthy, "automated_remediation": False}


def rbac_audit(grants: list[dict[str, Any]]) -> dict[str, Any]:
    privileged = {"trade_live", "approve_release", "manage_secrets", "disable_risk"}
    findings = []
    for grant in grants:
        perms = set(grant.get("permissions", []))
        risky = sorted(perms & privileged)
        if risky:
            findings.append({"principal": grant.get("principal", "unknown"),
                             "privileged_permissions": risky})
    return {"findings": findings, "status": "review_required" if findings else "no_privileged_grants_detected",
            "policy_enforced": False, "live_trading_enabled": False}


def workflow_gate(state: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "data_quality_passed": bool(state.get("data_quality_passed")),
        "walk_forward_passed": bool(state.get("walk_forward_passed")),
        "untouched_oos_passed": bool(state.get("untouched_oos_passed")),
        "paper_trading_passed": bool(state.get("paper_trading_passed")),
        "risk_approval": bool(state.get("risk_approval")),
        "human_approval": bool(state.get("human_approval")),
    }
    ready = all(checks.values())
    return {"checks": checks, "research_workflow_ready_for_review": ready,
            "live_order_routing_enabled": False,
            "decision": "eligible_for_separate_governance_review" if ready else "blocked",
            "risk_gate_bypass": False}
