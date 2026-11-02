"""Phase 10 capital-protection and institutional intelligence prototypes.

All calculations use caller-supplied data. No vendor failover, autonomous execution,
model deployment, or live order routing is performed.
"""
from __future__ import annotations

from math import isfinite
from statistics import mean, pstdev
from typing import Any


def _num(value: Any, field: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return result


def monitor_portfolio_risk(positions: list[dict[str, Any]], nav: float,
                           max_gross_leverage: float = 2.0,
                           max_name_weight: float = 0.25,
                           returns: list[float] | None = None,
                           confidence: float = 0.95) -> dict[str, Any]:
    nav = _num(nav, "nav", 0)
    if nav <= 0:
        raise ValueError("nav must be positive")
    leverage = _num(max_gross_leverage, "max_gross_leverage", 0)
    name_limit = _num(max_name_weight, "max_name_weight", 0)
    if leverage <= 0 or name_limit > 1 or not 0.5 < confidence < 1:
        raise ValueError("invalid risk limits or confidence")
    by_asset: dict[str, float] = {}
    gross = 0.0
    for row in positions:
        if not isinstance(row, dict) or not isinstance(row.get("asset"), str) or not row["asset"]:
            raise ValueError("each position requires an asset")
        value = _num(row.get("market_value"), "market_value")
        by_asset[row["asset"]] = by_asset.get(row["asset"], 0.0) + value
        gross += abs(value)
    weights = {k: abs(v) / nav for k, v in by_asset.items()}
    breaches = []
    if gross / nav > leverage:
        breaches.append("gross_leverage")
    if any(v > name_limit for v in weights.values()):
        breaches.append("single_name_concentration")
    var_es = None
    if returns is not None:
        sample = sorted(_num(x, "returns") for x in returns)
        if len(sample) < 20 or any(x < -1 for x in sample):
            raise ValueError("returns require at least 20 observations and must be >= -1")
        index = max(0, min(len(sample)-1, int((1-confidence)*len(sample))))
        var = max(0.0, -sample[index])
        tail = [x for x in sample if x <= sample[index]]
        var_es = {"historical_var": var, "expected_shortfall": max(0.0, -mean(tail))}
    return {"status": "breach" if breaches else "pass", "gross_exposure_nav": gross/nav,
            "asset_weights": weights, "breaches": breaches, "historical_risk": var_es,
            "greeks_included": False, "new_risk_allowed": not breaches}


def audit_vendor_failover(feeds: list[dict[str, Any]], max_age_seconds: float = 60,
                          conflict_tolerance_bps: float = 5) -> dict[str, Any]:
    age_limit = _num(max_age_seconds, "max_age_seconds", 0)
    tolerance = _num(conflict_tolerance_bps, "conflict_tolerance_bps", 0)
    usable = []
    issues = []
    for row in feeds:
        if not isinstance(row, dict):
            raise ValueError("each feed must be an object")
        vendor = row.get("vendor")
        if not isinstance(vendor, str) or not vendor:
            raise ValueError("vendor is required")
        age = _num(row.get("age_seconds"), "age_seconds", 0)
        price = _num(row.get("price"), "price", 0)
        authorized = row.get("authorized") is True
        healthy = row.get("healthy") is True
        if authorized and healthy and age <= age_limit and price > 0:
            usable.append({"vendor": vendor, "price": price, "age_seconds": age})
        else:
            issues.append({"vendor": vendor, "reason": "unauthorized, unhealthy, stale, or invalid"})
    conflict = False
    if len(usable) >= 2:
        mid = sorted(x["price"] for x in usable)[len(usable)//2]
        conflict = any(abs(x["price"]/mid-1)*10000 > tolerance for x in usable)
    selected = min(usable, key=lambda x: x["age_seconds"]) if usable and not conflict else None
    return {"status": "pass" if selected else "blocked", "selected_vendor": selected["vendor"] if selected else None,
            "eligible_vendors": [x["vendor"] for x in usable], "issues": issues,
            "price_conflict": conflict, "new_risk_allowed": selected is not None,
            "authorization_independently_verified": False}


def score_alpha_hypotheses(experiments: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for row in experiments:
        if not isinstance(row, dict) or not isinstance(row.get("hypothesis_id"), str):
            raise ValueError("each experiment requires hypothesis_id")
        sharpe = _num(row.get("oos_sharpe"), "oos_sharpe")
        trials = _num(row.get("trials"), "trials", 1)
        holdout = row.get("untouched_holdout") is True
        reproducible = row.get("reproducible") is True
        score = sharpe / (1 + 0.1 * max(0, trials-1))
        eligible = holdout and reproducible and row.get("walk_forward_pass") is True
        rows.append({"hypothesis_id": row["hypothesis_id"], "penalized_score": score,
                     "research_eligible": eligible})
    rows.sort(key=lambda x: x["penalized_score"], reverse=True)
    return {"ranked_hypotheses": rows, "multiple_testing_adjusted_p_values": False,
            "deployment_authorized": False}


def allocate_risk_budget(strategies: list[dict[str, Any]], total_risk_budget: float,
                         max_strategy_weight: float = 0.4) -> dict[str, Any]:
    budget = _num(total_risk_budget, "total_risk_budget", 0)
    cap = _num(max_strategy_weight, "max_strategy_weight", 0)
    if budget <= 0 or cap <= 0 or cap > 1:
        raise ValueError("budget must be positive and cap in (0,1]")
    rows = []
    for row in strategies:
        if not isinstance(row, dict) or not isinstance(row.get("strategy"), str):
            raise ValueError("each strategy requires a name")
        score = _num(row.get("risk_adjusted_score"), "risk_adjusted_score", 0)
        rows.append((row["strategy"], score))
    total = sum(x[1] for x in rows)
    if total <= 0:
        return {"allocations": [], "unallocated_budget": budget, "status": "blocked"}
    raw = [(name, score/total) for name, score in rows]
    weights = {name: min(weight, cap) for name, weight in raw}
    used = sum(weights.values())
    return {"allocations": [{"strategy": k, "weight": v, "risk_budget": v*budget}
                            for k, v in weights.items()],
            "unallocated_budget": budget * max(0, 1-used),
            "status": "pass", "execution_enabled": False,
            "note": "Capped weights are not redistributed; constraints may leave budget unused"}


def audit_agent_governance(agents: list[dict[str, Any]]) -> dict[str, Any]:
    required = ("agent_id", "role", "allowed_tools", "human_approval_required",
                "risk_gate_bypass", "audit_logging")
    issues = []
    for i, agent in enumerate(agents):
        if not isinstance(agent, dict):
            issues.append(f"agent {i}: must be an object")
            continue
        missing = [k for k in required if k not in agent]
        if missing:
            issues.append(f"agent {i}: missing {','.join(missing)}")
        if agent.get("risk_gate_bypass") is True:
            issues.append(f"agent {i}: risk-gate bypass prohibited")
        if agent.get("human_approval_required") is not True:
            issues.append(f"agent {i}: human approval must be required")
        if agent.get("audit_logging") is not True:
            issues.append(f"agent {i}: audit logging required")
        if not isinstance(agent.get("allowed_tools"), list):
            issues.append(f"agent {i}: allowed_tools must be a list")
    return {"status": "pass" if not issues else "blocked", "issues": issues,
            "agents_allowed_to_bypass_risk": [], "production_security_certified": False}


def analyze_execution_quality(trades: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for trade in trades:
        if not isinstance(trade, dict):
            raise ValueError("each trade must be an object")
        qty = _num(trade.get("filled_quantity"), "filled_quantity", 0)
        arrival = _num(trade.get("arrival_price"), "arrival_price", 0)
        fill = _num(trade.get("average_fill_price"), "average_fill_price", 0)
        side = trade.get("side")
        if qty <= 0 or arrival <= 0 or fill <= 0 or side not in ("buy", "sell"):
            raise ValueError("trade requires positive quantity/prices and buy or sell side")
        signed_bps = ((fill/arrival)-1) * (1 if side == "buy" else -1) * 10000
        rows.append({"trade_id": str(trade.get("trade_id", "")),
                     "implementation_shortfall_bps": signed_bps,
                     "filled_quantity": qty})
    return {"trades": rows,
            "mean_implementation_shortfall_bps": mean(x["implementation_shortfall_bps"] for x in rows) if rows else None,
            "venue_best_execution_determined": False, "calibrated_tca": False}


def simulate_systemic_stress(exposures: list[dict[str, Any]], shocks: dict[str, float],
                             nav: float) -> dict[str, Any]:
    nav = _num(nav, "nav", 0)
    if nav <= 0:
        raise ValueError("nav must be positive")
    shock_map = {k: _num(v, f"shock.{k}") for k, v in shocks.items()}
    pnl = 0.0
    for row in exposures:
        if not isinstance(row, dict) or not isinstance(row.get("factor"), str):
            raise ValueError("each exposure requires factor")
        amount = _num(row.get("exposure"), "exposure")
        pnl += amount * shock_map.get(row["factor"], 0.0)
    return {"scenario_pnl": pnl, "scenario_return": pnl/nav,
            "stressed_nav": nav+pnl, "liquidation_simulated": False,
            "probability_assigned": False}


def evaluate_strategy_approval(evidence: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object")
    gates = ("data_lineage_pass", "backtest_integrity_pass", "walk_forward_pass",
             "untouched_holdout_pass", "multiple_testing_review_pass",
             "risk_signoff", "paper_trading_pass", "human_approval")
    missing = [key for key in gates if evidence.get(key) is not True]
    return {"status": "eligible_for_review" if not missing else "rejected",
            "failed_gates": missing, "live_deployment_authorized": False,
            "final_authorization_requires_separate_security_and_operations_review": True}
