"""Deterministic, research-only foundations for OSIRIS EYE capability groups."""
from math import isfinite, sqrt


def portfolio_risk(returns_by_asset):
    if len(returns_by_asset) < 2:
        raise ValueError("at least two assets required")
    rows = {k: [float(x) for x in v] for k, v in returns_by_asset.items()}
    if any(len(v) < 2 or not all(isfinite(x) and x > -1 for x in v) for v in rows.values()):
        raise ValueError("asset returns must be aligned finite values greater than -100%")
    if len({len(v) for v in rows.values()}) != 1:
        raise ValueError("all asset series must have equal length")
    n = len(rows)
    portfolio = [sum(v[i] for v in rows.values()) / n for i in range(len(next(iter(rows.values()))))]
    ordered = sorted(portfolio)
    tail = ordered[:max(1, int(len(ordered) * 0.05))]
    equity = peak = 1.0
    drawdown = 0.0
    for r in portfolio:
        equity *= 1 + r
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak)
    return {"method": "equal_weight_diagnostic", "weights": {k: 1/n for k in rows},
            "historical_var_95": -tail[-1], "expected_shortfall_95": -sum(tail)/len(tail),
            "max_drawdown": drawdown, "observations": len(portfolio),
            "status": "research_only"}


def options_surface_audit(points):
    if not points:
        raise ValueError("points must not be empty")
    seen, issues, ivs = set(), [], []
    for i, p in enumerate(points):
        try:
            strike, dte, iv = float(p["strike"]), float(p["days_to_expiry"]), float(p["implied_volatility"])
            if not all(isfinite(x) for x in (strike, dte, iv)) or strike <= 0 or dte < 0 or iv <= 0:
                raise ValueError()
            key = (dte, strike)
            if key in seen:
                issues.append({"index": i, "code": "duplicate_expiry_strike"})
            seen.add(key)
            ivs.append(iv)
        except (KeyError, TypeError, ValueError):
            issues.append({"index": i, "code": "invalid_point"})
    return {"points_checked": len(points), "valid_points": len(ivs), "issues": issues,
            "iv_min": min(ivs) if ivs else None, "iv_max": max(ivs) if ivs else None,
            "surface_arbitrage_checked": False, "status": "diagnostic_only"}


def cross_market_correlation(series):
    if len(series) < 2:
        raise ValueError("at least two market series required")
    clean = {k: [float(x) for x in v] for k, v in series.items()}
    if any(len(v) < 2 or not all(isfinite(x) for x in v) for v in clean.values()) or len({len(v) for v in clean.values()}) != 1:
        raise ValueError("market series must be aligned finite values")
    names, pairs = list(clean), []
    for i, a in enumerate(names):
        for b in names[i+1:]:
            x, y = clean[a], clean[b]
            mx, my = sum(x)/len(x), sum(y)/len(y)
            dx, dy = [v-mx for v in x], [v-my for v in y]
            den = sqrt(sum(v*v for v in dx)*sum(v*v for v in dy))
            pairs.append({"left": a, "right": b, "correlation": sum(u*v for u,v in zip(dx,dy))/den if den else None})
    return {"pairs": pairs, "alignment": "caller_supplied_order", "causality_established": False}


def alternative_data_intake(events, outcome_returns):
    if len(outcome_returns) < 2 or not all(isfinite(float(x)) for x in outcome_returns):
        raise ValueError("outcome_returns must contain at least two finite values")
    valid = sum(1 for e in events if isinstance(e, dict) and e.get("source") and e.get("event_time") and e.get("available_at"))
    return {"events_received": len(events), "events_with_provenance": valid,
            "outcome_observations": len(outcome_returns), "finding": "not_evaluated",
            "causal_inference": False, "status": "research_intake_only"}


def agent_research_proposal(objective, roles):
    allowed = {"market_data", "macro", "options", "microstructure", "portfolio_risk", "skeptic"}
    if not isinstance(objective, str) or not objective.strip() or len(objective) > 2000:
        raise ValueError("objective must contain 1-2000 characters")
    if not roles or any(role not in allowed for role in roles):
        raise ValueError("roles must use approved research role names")
    return {"objective": objective, "assigned_roles": list(dict.fromkeys(roles)),
            "execution": "not_started", "permissions": "read_only_research",
            "human_approval_required": True, "risk_gate_bypass": False}


def command_center_snapshot(modules):
    return {"platform": "OSIRIS EYE", "mode": "research_only", "modules": modules,
            "module_count": len(modules), "live_market_data": False,
            "broker_connected": False, "order_routing_enabled": False, "status": "scaffold"}
