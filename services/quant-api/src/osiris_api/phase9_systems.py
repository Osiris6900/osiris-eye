"""Phase 9 institutional multi-asset controls.

Deterministic, caller-supplied-data diagnostics only. No external feeds, tax advice,
probability forecasts, production failover, or live execution are provided.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from math import isfinite
from statistics import mean, pstdev
from typing import Any


def _number(value: Any, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return result


def _aware(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def audit_global_instruments(instruments: list[dict[str, Any]]) -> dict[str, Any]:
    required = ("instrument_id", "asset_class", "currency", "exchange", "calendar", "timezone")
    seen: set[str] = set()
    issues: list[str] = []
    for i, item in enumerate(instruments):
        if not isinstance(item, dict):
            raise ValueError("each instrument must be an object")
        missing = [key for key in required if not item.get(key)]
        if missing:
            issues.append(f"row {i}: missing {','.join(missing)}")
        ident = item.get("instrument_id")
        if isinstance(ident, str):
            if ident in seen:
                issues.append(f"row {i}: duplicate instrument_id {ident}")
            seen.add(ident)
        else:
            issues.append(f"row {i}: instrument_id must be a string")
        if item.get("asset_class") in ("future", "option"):
            try:
                multiplier = _number(item.get("contract_multiplier"), "contract_multiplier", minimum=0)
                if multiplier <= 0:
                    raise ValueError("contract_multiplier must be positive")
            except ValueError:
                issues.append(f"row {i}: derivative requires a positive contract_multiplier")
            try:
                expiry = item.get("expiry")
                if not isinstance(expiry, str):
                    raise ValueError("expiry must be an ISO date")
                date.fromisoformat(expiry)
            except ValueError:
                issues.append(f"row {i}: derivative expiry must be an ISO-8601 date (YYYY-MM-DD)")
    return {"status": "pass" if not issues else "blocked", "instrument_count": len(instruments),
            "issues": issues, "new_risk_allowed": not issues,
            "calendar_and_timezone_verified_externally": False}


def analyze_counterparty_risk(exposures: list[dict[str, Any]], nav: float,
                              max_counterparty_weight: float = 0.25) -> dict[str, Any]:
    nav = _number(nav, "nav", minimum=0)
    limit = _number(max_counterparty_weight, "max_counterparty_weight", minimum=0)
    if nav <= 0 or limit > 1:
        raise ValueError("nav must be positive and max_counterparty_weight must be <= 1")
    totals: dict[str, float] = defaultdict(float)
    categories: dict[str, set[str]] = defaultdict(set)
    for row in exposures:
        if not isinstance(row, dict):
            raise ValueError("each exposure must be an object")
        name = row.get("counterparty")
        if not isinstance(name, str) or not name:
            raise ValueError("counterparty is required")
        totals[name] += _number(row.get("exposure"), "exposure", minimum=0)
        if row.get("role"):
            categories[name].add(str(row["role"]))
    weights = {name: value / nav for name, value in totals.items()}
    breaches = [name for name, weight in weights.items() if weight > limit]
    return {"status": "alert" if breaches else "pass", "counterparty_weights": weights,
            "counterparty_roles": {k: sorted(v) for k, v in categories.items()},
            "breaches": breaches, "gross_counterparty_exposure_nav": sum(totals.values()) / nav,
            "limit": limit, "creditworthiness_assessed": False}


def process_corporate_actions(actions: list[dict[str, Any]],
                              tax_lots: list[dict[str, Any]]) -> dict[str, Any]:
    valid_types = {"split", "cash_dividend", "symbol_change", "merger"}
    normalized = []
    issues = []
    for i, action in enumerate(actions):
        if not isinstance(action, dict):
            issues.append(f"action {i}: must be an object")
            continue
        kind = action.get("type")
        if kind not in valid_types:
            issues.append(f"action {i}: unsupported type")
            continue
        if not action.get("instrument_id") or not action.get("effective_at"):
            issues.append(f"action {i}: instrument_id and effective_at required")
            continue
        _aware(action["effective_at"], "effective_at")
        item = dict(action)
        if kind == "split":
            ratio = _number(action.get("ratio"), "ratio", minimum=0)
            if ratio <= 0:
                raise ValueError("split ratio must be positive")
            item["ratio"] = ratio
        if kind == "cash_dividend":
            item["cash_per_share"] = _number(action.get("cash_per_share"), "cash_per_share", minimum=0)
        normalized.append(item)
    lot_totals: dict[str, float] = defaultdict(float)
    for lot in tax_lots:
        if not isinstance(lot, dict):
            raise ValueError("each tax lot must be an object")
        instrument = lot.get("instrument_id")
        if not instrument:
            raise ValueError("tax lot instrument_id is required")
        quantity = _number(lot.get("quantity"), "lot quantity", minimum=0)
        basis = _number(lot.get("cost_basis"), "lot cost_basis", minimum=0)
        lot_totals[instrument] += quantity * basis
    return {"status": "pass" if not issues else "review_required", "actions": normalized,
            "issues": issues, "aggregate_lot_cost_basis": dict(lot_totals),
            "tax_lot_method": "aggregate quantity x supplied per-unit basis",
            "tax_liability_calculated": False, "actions_applied_to_positions": False}


def estimate_portfolio_capacity(positions: list[dict[str, Any]],
                                participation_rate: float = 0.10,
                                stress_volume_haircut: float = 0.50) -> dict[str, Any]:
    participation = _number(participation_rate, "participation_rate", minimum=0)
    haircut = _number(stress_volume_haircut, "stress_volume_haircut", minimum=0)
    if participation <= 0 or participation > 1 or haircut > 1:
        raise ValueError("participation_rate must be in (0,1] and haircut in [0,1]")
    rows = []
    for row in positions:
        if not isinstance(row, dict):
            raise ValueError("each position must be an object")
        asset = row.get("asset")
        if not isinstance(asset, str) or not asset:
            raise ValueError("asset is required")
        quantity = _number(row.get("quantity"), "quantity", minimum=0)
        adv = _number(row.get("adv_quantity"), "adv_quantity", minimum=0)
        daily_capacity = adv * participation
        stressed_capacity = daily_capacity * (1 - haircut)
        rows.append({"asset": asset, "estimated_exit_days": quantity / daily_capacity if daily_capacity else None,
                     "stressed_exit_days": quantity / stressed_capacity if stressed_capacity else None,
                     "participation_rate": participation, "stressed_daily_capacity": stressed_capacity})
    return {"positions": rows, "capacity_is_calibrated": False,
            "market_impact_or_crowding_measured": False}


def aggregate_derivatives_exposure(positions: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {key: 0.0 for key in ("delta", "gamma", "vega", "theta", "rho")}
    by_underlying: dict[str, dict[str, float]] = {}
    issues = []
    for i, row in enumerate(positions):
        if not isinstance(row, dict):
            raise ValueError("each derivatives position must be an object")
        underlying = row.get("underlying")
        if not isinstance(underlying, str) or not underlying:
            raise ValueError("underlying is required")
        multiplier = _number(row.get("multiplier", 1), "multiplier", minimum=0)
        quantity = _number(row.get("quantity"), "quantity")
        bucket = by_underlying.setdefault(underlying, dict.fromkeys(totals, 0.0))
        for greek in totals:
            value = _number(row.get(greek, 0), greek) * quantity * multiplier
            totals[greek] += value
            bucket[greek] += value
        if row.get("expiry"):
            _aware(row["expiry"], "expiry")
        else:
            issues.append(f"row {i}: expiry missing")
    return {"aggregate_greeks": totals, "by_underlying": by_underlying,
            "issues": issues, "surface_and_vol_of_vol_calibrated": False}


def audit_financial_graph(nodes: list[dict[str, Any]],
                          edges: list[dict[str, Any]]) -> dict[str, Any]:
    if any(not isinstance(node, dict) for node in nodes):
        raise ValueError("each graph node must be an object")
    ids = [node.get("id") for node in nodes]
    known = {ident for ident in ids if isinstance(ident, str) and ident}
    issues = []
    if len(known) != len(ids):
        issues.append("node ids missing, invalid, or duplicated")
    for i, edge in enumerate(edges):
        if not isinstance(edge, dict):
            issues.append(f"edge {i}: must be an object")
            continue
        source, target = edge.get("source"), edge.get("target")
        if not isinstance(source, str) or not isinstance(target, str) or source not in known or target not in known:
            issues.append(f"edge {i}: endpoint not found")
        if not edge.get("relationship"):
            issues.append(f"edge {i}: relationship missing")
        if edge.get("observed_at"):
            _aware(edge["observed_at"], "observed_at")
    return {"status": "pass" if not issues else "blocked", "node_count": len(nodes),
            "edge_count": len(edges), "issues": issues,
            "causal_relationships_inferred": False}


def simulate_disaster_recovery(services: list[dict[str, Any]]) -> dict[str, Any]:
    if not services:
        return {"status": "blocked", "services": [],
                "issues": ["at least one service recovery test is required"],
                "production_failover_executed": False}
    results = []
    for row in services:
        if not isinstance(row, dict):
            raise ValueError("each service recovery result must be an object")
        name = row.get("service")
        if not isinstance(name, str) or not name:
            raise ValueError("service name is required")
        rto = _number(row.get("observed_rto_seconds"), "observed_rto_seconds", minimum=0)
        rto_target = _number(row.get("rto_target_seconds"), "rto_target_seconds", minimum=0)
        rpo = _number(row.get("observed_rpo_seconds"), "observed_rpo_seconds", minimum=0)
        rpo_target = _number(row.get("rpo_target_seconds"), "rpo_target_seconds", minimum=0)
        passed = rto <= rto_target and rpo <= rpo_target and row.get("restore_tested") is True
        results.append({"service": name, "passed": passed,
                        "rto_within_target": rto <= rto_target,
                        "rpo_within_target": rpo <= rpo_target})
    return {"status": "pass" if all(x["passed"] for x in results) else "blocked",
            "services": results, "production_failover_executed": False}


def benchmark_performance(portfolio_returns: list[float],
                          benchmark_returns: list[float],
                          periods_per_year: int = 252) -> dict[str, Any]:
    if len(portfolio_returns) != len(benchmark_returns) or len(portfolio_returns) < 2:
        raise ValueError("portfolio and benchmark returns must have equal length >= 2")
    if isinstance(periods_per_year, bool) or not isinstance(periods_per_year, int) or periods_per_year < 1:
        raise ValueError("periods_per_year must be a positive integer")
    p = [_number(x, "portfolio return") for x in portfolio_returns]
    b = [_number(x, "benchmark return") for x in benchmark_returns]
    active = [x - y for x, y in zip(p, b)]
    tracking_error = pstdev(active) * periods_per_year ** 0.5
    return {"portfolio_total_return": _compound(p), "benchmark_total_return": _compound(b),
            "active_total_return": _compound(p) - _compound(b),
            "annualized_tracking_error": tracking_error,
            "information_ratio": mean(active) * periods_per_year ** 0.5 / tracking_error if tracking_error else None,
            "periods": len(p), "fx_and_factor_attribution_included": False}


def _compound(returns: list[float]) -> float:
    value = 1.0
    for item in returns:
        value *= 1 + item
    return value - 1
