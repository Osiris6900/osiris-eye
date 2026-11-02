"""Phase 8 institutional controls: deterministic diagnostics, never live execution."""
from datetime import datetime
from math import isfinite


def _num(x, name, lo=None, hi=None):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not isfinite(x):
        raise ValueError(f"{name} must be finite")
    x = float(x)
    if lo is not None and x < lo or hi is not None and x > hi:
        raise ValueError(f"{name} outside allowed range")
    return x


def _ts(x, name):
    try:
        d = datetime.fromisoformat(x.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"{name} must be timezone-aware ISO-8601") from exc
    if d.tzinfo is None or d.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware ISO-8601")
    return d


def audit_data_lineage(records):
    if not isinstance(records, list) or not records:
        raise ValueError("records must be non-empty")
    seen, issues = set(), []
    for i, r in enumerate(records):
        if not isinstance(r, dict):
            raise ValueError("records must contain objects")
        required = ("record_id", "source", "license_status", "event_time", "available_at")
        missing = [k for k in required if not r.get(k)]
        if missing:
            issues.append({"index": i, "code": "missing_fields", "fields": missing})
            continue
        if r["record_id"] in seen:
            issues.append({"index": i, "code": "duplicate_record_id"})
        seen.add(r["record_id"])
        if _ts(r["available_at"], "available_at") < _ts(r["event_time"], "event_time"):
            issues.append({"index": i, "code": "availability_precedes_event"})
        if r["license_status"] != "authorized":
            issues.append({"index": i, "code": "license_not_verified"})
    return {"status": "pass" if not issues else "blocked", "records_checked": len(records),
            "issues": issues, "new_risk_allowed": not issues,
            "license_verification": "operator_declared_not_independently_verified"}


def audit_backtest_integrity(evidence):
    keys = ("point_in_time_data", "corporate_actions_adjusted", "delistings_included",
            "costs_modeled", "walk_forward_passed", "untouched_holdout_passed",
            "multiple_testing_disclosed")
    if not isinstance(evidence, dict) or any(not isinstance(evidence.get(k), bool) for k in keys):
        raise ValueError("all backtest evidence controls must be booleans")
    failed = [k for k in keys if evidence[k] is not True]
    return {"integrity": "pass" if not failed else "review_required",
            "failed_controls": failed, "deployment_authorized": False,
            "note": "Caller-supplied evidence; not independently verified."}


def portfolio_risk_governor(positions, max_gross_exposure, max_single_name_weight, current_drawdown, max_drawdown):
    if not isinstance(positions, list) or not positions:
        raise ValueError("positions must be non-empty")
    gross_limit = _num(max_gross_exposure, "max_gross_exposure", 0)
    name_limit = _num(max_single_name_weight, "max_single_name_weight", 0, 1)
    dd = _num(current_drawdown, "current_drawdown", 0, 1)
    dd_limit = _num(max_drawdown, "max_drawdown", 0, 1)
    nav_values = []
    asset_values = {}
    for p in positions:
        if not isinstance(p, dict) or not p.get("asset"):
            raise ValueError("each position requires asset")
        nav_values.append(_num(p.get("nav"), "nav", 0))
        market_value = _num(p.get("market_value"), "market_value", 0)
        asset_values[p["asset"]] = asset_values.get(p["asset"], 0.0) + market_value
    if not nav_values or nav_values[0] <= 0:
        raise ValueError("portfolio nav must be positive")
    nav = nav_values[0]
    if any(abs(value - nav) > max(1e-8, nav * 1e-8) for value in nav_values[1:]):
        raise ValueError("all position rows must reference the same portfolio nav")
    gross_value = sum(asset_values.values())
    gross = gross_value / nav
    largest = max(asset_values.values()) / nav if asset_values else 0.0
    breaches = []
    if gross > gross_limit: breaches.append("gross_exposure_limit")
    if largest > name_limit: breaches.append("single_name_concentration_limit")
    if dd >= dd_limit: breaches.append("drawdown_limit")
    return {"status": "blocked" if breaches else "within_limits", "gross_exposure": gross,
            "largest_name_weight": largest, "breaches": breaches,
            "new_risk_allowed": not breaches, "execution_enabled": False}


def catalyst_impact_scenarios(weights, scenarios):
    if not isinstance(weights, dict) or not weights or not isinstance(scenarios, dict) or not scenarios:
        raise ValueError("weights and scenarios must be non-empty objects")
    w = {a: _num(v, f"weight {a}") for a, v in weights.items()}
    out = {}
    for name, shocks in scenarios.items():
        if not isinstance(shocks, dict) or set(w) - set(shocks):
            raise ValueError(f"scenario {name} must include a shock for every asset")
        impact = sum(w[a] * _num(shocks[a], f"shock {a}", -1, 10) for a in w)
        out[name] = {"portfolio_return_impact": impact, "units": "return_fraction"}
    return {"scenarios": out, "forecast_or_probability": False, "execution_enabled": False}


def observe_model_health(reference, current, max_mean_shift=0.5, max_std_ratio=2.0):
    if not isinstance(reference, list) or not isinstance(current, list) or min(len(reference), len(current)) < 2:
        raise ValueError("reference and current require at least two observations")
    ref = [_num(x, "reference") for x in reference]
    cur = [_num(x, "current") for x in current]
    limit = _num(max_mean_shift, "max_mean_shift", 0)
    ratio_limit = _num(max_std_ratio, "max_std_ratio", 1)
    mr, mc = sum(ref)/len(ref), sum(cur)/len(cur)
    sr = (sum((x-mr)**2 for x in ref)/len(ref))**0.5
    sc = (sum((x-mc)**2 for x in cur)/len(cur))**0.5
    ratio = sc/sr if sr > 1e-12 else None
    alerts = []
    if abs(mc-mr) > limit: alerts.append("mean_shift")
    if (ratio is not None and ratio > ratio_limit) or (ratio is None and sc > 1e-12):
        alerts.append("volatility_shift")
    return {"status": "alert" if alerts else "nominal", "alerts": alerts,
            "mean_shift": abs(mc-mr), "std_ratio": ratio,
            "std_ratio_status": "undefined_zero_reference_variance" if ratio is None else "defined",
            "deployment_authorized": False}


def reconcile_paper_trades(orders, fills, positions):
    if not all(isinstance(x, list) for x in (orders, fills, positions)):
        raise ValueError("orders, fills and positions must be lists")
    order_map = {}
    for o in orders:
        if not isinstance(o, dict) or not o.get("order_id") or o["order_id"] in order_map:
            raise ValueError("orders require unique order_id")
        order_map[o["order_id"]] = _num(o.get("quantity"), "order quantity", 0)
    filled = dict.fromkeys(order_map, 0.0)
    expected = {}
    for f in fills:
        if not isinstance(f, dict) or f.get("order_id") not in order_map:
            raise ValueError("fill references unknown order")
        q = _num(f.get("quantity"), "fill quantity", 0)
        _num(f.get("price"), "fill price", 0)
        oid = f["order_id"]
        filled[oid] += q
        if filled[oid] > order_map[oid] + 1e-9: raise ValueError("fills exceed order quantity")
    for o in orders:
        if not o.get("asset") or o.get("side") not in ("buy", "sell"):
            raise ValueError("orders require asset and buy/sell side")
        sign = 1 if o["side"] == "buy" else -1
        expected[o["asset"]] = expected.get(o["asset"], 0) + sign*filled[o["order_id"]]
    actual = {}
    for p in positions:
        if not isinstance(p, dict) or not p.get("asset"): raise ValueError("positions require asset")
        actual[p["asset"]] = _num(p.get("quantity"), "position quantity")
    breaks = {a: expected.get(a, 0)-actual.get(a, 0) for a in set(expected)|set(actual)}
    breaks = {a: v for a, v in breaks.items() if abs(v) > 1e-9}
    return {"status": "reconciled" if not breaks else "breaks_found",
            "filled_quantity_by_order": filled, "position_breaks": breaks, "live_execution": False}


def audit_security_resilience(controls):
    keys = ("mfa", "least_privilege", "secrets_managed", "audit_logging",
            "backup_restore_tested", "kill_switch_tested")
    if not isinstance(controls, dict) or any(not isinstance(controls.get(k), bool) for k in keys):
        raise ValueError("all security controls must be booleans")
    failed = [k for k in keys if not controls[k]]
    return {"status": "blocked" if failed else "review_ready", "failed_controls": failed,
            "new_risk_allowed": not failed, "live_execution_enabled": False}


def orchestrate_research_workflow(gates):
    keys = ("data_lineage_passed", "backtest_integrity_passed", "walk_forward_passed",
            "untouched_holdout_passed", "risk_review_passed", "paper_reconciliation_passed",
            "security_review_passed", "human_approval")
    if not isinstance(gates, dict) or any(not isinstance(gates.get(k), bool) for k in keys):
        raise ValueError("all workflow gates must be booleans")
    failed = [k for k in keys if not gates[k]]
    return {"workflow_status": "ready_for_human_review" if not failed else "blocked",
            "failed_gates": failed, "live_order_routing_enabled": False,
            "agents_can_bypass_risk": False}
