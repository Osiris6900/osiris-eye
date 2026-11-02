"""Phase 11 production-oriented portfolio and execution control primitives.

All functions are deterministic research/paper controls. No broker connectivity,
live order routing, or independently verified vendor entitlement is provided.
"""
from __future__ import annotations

import math
from statistics import mean


def _number(value, name, *, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    value = float(value)
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return value


def reconcile_ledger(positions, broker_positions, cash, broker_cash, tolerance=1e-6):
    if not isinstance(positions, list) or not isinstance(broker_positions, list):
        raise ValueError("positions and broker_positions must be lists")
    tolerance = _number(tolerance, "tolerance", minimum=0)
    cash = _number(cash, "cash")
    broker_cash = _number(broker_cash, "broker_cash")
    def aggregate(rows, key):
        totals = {}
        for row in rows:
            if not isinstance(row, dict) or not row.get("asset"):
                raise ValueError("each position requires an asset")
            qty = _number(row.get("quantity"), "quantity")
            totals[row["asset"]] = totals.get(row["asset"], 0.0) + qty
        return totals
    internal, external = aggregate(positions, "asset"), aggregate(broker_positions, "asset")
    diffs = {a: internal.get(a, 0.0) - external.get(a, 0.0)
             for a in sorted(set(internal) | set(external))}
    breaks = {a: d for a, d in diffs.items() if abs(d) > tolerance}
    cash_diff = cash - broker_cash
    return {"status": "reconciled" if not breaks and abs(cash_diff) <= tolerance else "breaks_found",
            "position_differences": diffs, "cash_difference": cash_diff,
            "new_risk_allowed": not breaks and abs(cash_diff) <= tolerance}


def simulate_order_lifecycle(order):
    if not isinstance(order, dict):
        raise ValueError("order must be an object")
    qty = _number(order.get("quantity"), "quantity", minimum=0)
    if qty <= 0:
        raise ValueError("quantity must be positive")
    if "side" in order and order["side"] not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    filled = _number(order.get("filled_quantity", 0), "filled_quantity", minimum=0)
    if filled > qty:
        raise ValueError("filled_quantity cannot exceed quantity")
    status = str(order.get("status", "accepted")).lower()
    allowed = {"accepted", "partially_filled", "filled", "cancelled", "rejected"}
    if status not in allowed:
        raise ValueError("unsupported order status")
    if status == "filled" and filled != qty:
        raise ValueError("filled order must have filled_quantity equal to quantity")
    if status in {"accepted", "cancelled", "rejected"} and filled != 0:
        raise ValueError(f"{status} order cannot contain fills in this lifecycle snapshot")
    if status == "partially_filled" and not 0 < filled < qty:
        raise ValueError("partially_filled requires a partial quantity")
    remaining = qty - filled
    return {"order_id": str(order.get("order_id", "")), "status": status,
            "quantity": qty, "filled_quantity": filled, "remaining_quantity": remaining,
            "terminal": status in {"filled", "cancelled", "rejected"},
            "live_routing_enabled": False}


def calibrate_transaction_costs(observations):
    if not observations:
        raise ValueError("at least one historical execution observation is required")
    buckets = {}
    for row in observations:
        if not isinstance(row, dict) or not row.get("bucket"):
            raise ValueError("each observation requires a bucket")
        cost = _number(row.get("realized_cost_bps"), "realized_cost_bps")
        buckets.setdefault(str(row["bucket"]), []).append(cost)
    return {"calibration_status": "historical_sample_summary",
            "buckets": {k: {"observations": len(v), "mean_cost_bps": mean(v),
                            "min_cost_bps": min(v), "max_cost_bps": max(v)}
                        for k, v in sorted(buckets.items())},
            "production_calibrated": False}


def options_greeks_risk(positions):
    if not isinstance(positions, list):
        raise ValueError("positions must be a list")
    totals = {k: 0.0 for k in ("delta", "gamma", "vega", "theta")}
    for row in positions:
        if not isinstance(row, dict):
            raise ValueError("each options position must be an object")
        qty = _number(row.get("quantity"), "quantity")
        multiplier = _number(row.get("multiplier", 100), "multiplier", minimum=0)
        for greek in totals:
            totals[greek] += _number(row.get(greek, 0), greek) * qty * multiplier
    return {"aggregate_greeks": totals, "positions": len(positions),
            "market_value_and_vol_surface_revaluation_included": False}


def margin_funding_stress(positions, available_funds, maintenance_margin, funding_bps=0):
    if not isinstance(positions, list):
        raise ValueError("positions must be a list")
    funds = _number(available_funds, "available_funds", minimum=0)
    margin = _number(maintenance_margin, "maintenance_margin", minimum=0)
    funding = _number(funding_bps, "funding_bps", minimum=0)
    gross = sum(abs(_number(x.get("notional"), "notional"))
                for x in positions if isinstance(x, dict))
    if any(not isinstance(x, dict) for x in positions):
        raise ValueError("each position must be an object")
    shortfall = max(0.0, margin - funds)
    return {"gross_notional": gross, "maintenance_margin": margin,
            "available_funds": funds, "margin_shortfall": shortfall,
            "estimated_daily_funding_cost": gross * funding / 10000 / 360,
            "status": "margin_breach" if shortfall > 0 else "within_supplied_margin",
            "broker_margin_rules_verified": False}


def plan_position_exit(positions, adv_participation=0.1, stress_multiplier=2.0):
    if not isinstance(positions, list):
        raise ValueError("positions must be a list")
    participation = _number(adv_participation, "adv_participation", minimum=0.000001, maximum=1)
    stress = _number(stress_multiplier, "stress_multiplier", minimum=1)
    plans = []
    for row in positions:
        if not isinstance(row, dict):
            raise ValueError("each position must be an object")
        qty = abs(_number(row.get("quantity"), "quantity"))
        adv = _number(row.get("adv_quantity"), "adv_quantity", minimum=0.000001)
        daily_capacity = adv * participation
        days = qty / daily_capacity
        plans.append({"asset": str(row.get("asset", "")), "base_exit_days": days,
                      "stressed_exit_days": days * stress,
                      "daily_quantity_limit": daily_capacity})
    return {"plans": plans, "participation_assumption": participation,
            "stress_multiplier": stress, "market_impact_calibrated": False}


def evaluate_model_rollout(metrics):
    if not isinstance(metrics, dict):
        raise ValueError("metrics must be an object")
    required = ("shadow_period_complete", "performance_within_tolerance",
                "drift_within_limit", "risk_checks_pass", "human_approval")
    failed = [key for key in required if metrics.get(key) is not True]
    return {"status": "canary_eligible" if not failed else "blocked",
            "failed_gates": failed, "automatic_rollback_recommended": bool(failed),
            "production_deployment_authorized": False}


def evaluate_kill_switch(health, risk_breaches, data_critical, manual_halt=False):
    if not isinstance(health, dict):
        raise ValueError("health must be an object")
    if not isinstance(manual_halt, bool):
        raise ValueError("manual_halt must be a boolean")
    if not isinstance(data_critical, bool):
        raise ValueError("data_critical must be a boolean")
    unhealthy = sorted(str(k) for k, v in health.items() if v is not True)
    breach_count = int(_number(risk_breaches, "risk_breaches", minimum=0))
    if float(breach_count) != risk_breaches:
        raise ValueError("risk_breaches must be an integer")
    halt = bool(unhealthy or breach_count or data_critical is not False or manual_halt)
    return {"action": "BLOCK_NEW_RISK" if halt else "MONITOR",
            "unhealthy_components": unhealthy, "risk_breach_count": breach_count,
            "critical_data_clear": data_critical is False,
            "manual_halt": bool(manual_halt), "live_orders_cancelled": False,
            "human_recovery_approval_required": halt}
