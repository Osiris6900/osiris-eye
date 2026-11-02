"""Phase 6 quantitative research diagnostics; no live trading or forecast guarantees."""
from __future__ import annotations

import math
from statistics import mean, pstdev


def _finite(values, name, minimum=1):
    if not isinstance(values, list) or len(values) < minimum:
        raise ValueError(f"{name} must contain at least {minimum} observations")
    out = [float(x) for x in values]
    if not all(math.isfinite(x) for x in out):
        raise ValueError(f"{name} must be finite")
    return out


def walk_forward_validation(returns, train_size, test_size, step=None):
    r = _finite(returns, "returns", 3)
    if train_size < 2 or test_size < 1 or train_size + test_size > len(r):
        raise ValueError("invalid walk-forward window sizes")
    step = test_size if step is None else step
    if step < 1:
        raise ValueError("step must be positive")
    folds, start = [], 0
    while start + train_size + test_size <= len(r):
        train = r[start:start + train_size]
        test = r[start + train_size:start + train_size + test_size]
        vol = pstdev(train)
        folds.append({"train_start": start, "train_end": start + train_size,
                      "test_start": start + train_size, "test_end": start + train_size + test_size,
                      "train_mean": mean(train), "test_mean": mean(test),
                      "train_volatility": vol, "test_observations": len(test)})
        start += step
    return {"folds": folds, "fold_count": len(folds),
            "validation": "chronological_expanding_origin_no_shuffling",
            "untouched_final_test_set_used": False,
            "warning": "Fold summaries are diagnostics, not proof of predictive skill."}


def portfolio_risk_diagnostics(returns, weights, periods_per_year=252):
    if not isinstance(returns, dict) or not returns:
        raise ValueError("returns must be a non-empty asset-to-series mapping")
    names = sorted(returns)
    series = {k: _finite(returns[k], f"returns[{k}]", 2) for k in names}
    n = len(series[names[0]])
    if any(len(v) != n for v in series.values()):
        raise ValueError("asset return series must have equal lengths")
    if set(weights) != set(names):
        raise ValueError("weights and return universes must match")
    w = {k: float(weights[k]) for k in names}
    if not all(math.isfinite(x) for x in w.values()):
        raise ValueError("weights must be finite")
    if not isinstance(periods_per_year, int) or periods_per_year < 1:
        raise ValueError("periods_per_year must be a positive integer")
    port = [sum(w[k] * series[k][i] for k in names) for i in range(n)]
    vol = pstdev(port) * math.sqrt(periods_per_year)
    equity, peak, max_dd = 1.0, 1.0, 0.0
    for x in port:
        equity *= 1 + x
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1)
    gross = sum(abs(x) for x in w.values())
    return {"portfolio_period_returns": port, "annualized_volatility": vol,
            "max_drawdown": max_dd, "gross_leverage": gross,
            "weight_concentration_hhi": sum((x / gross) ** 2 for x in w.values()) if gross else 0,
            "asset_count": len(names), "risk_status": "diagnostic_only"}


def execution_intelligence(quantity, adv, half_spread_bps, volatility, impact_coefficient=1.0, max_participation=0.1):
    vals = [quantity, adv, half_spread_bps, volatility, impact_coefficient, max_participation]
    if not all(math.isfinite(float(x)) for x in vals):
        raise ValueError("execution inputs must be finite")
    if quantity < 0 or adv <= 0 or half_spread_bps < 0 or volatility < 0 or impact_coefficient < 0 or not 0 < max_participation <= 1:
        raise ValueError("invalid execution inputs")
    participation = quantity / adv
    impact = impact_coefficient * volatility * math.sqrt(participation) * 10000 if participation else 0.0
    total = 2 * half_spread_bps + impact
    return {"participation_rate": participation, "estimated_impact_bps": impact,
            "estimated_round_trip_cost_bps": total,
            "capacity_status": "within_limit" if participation <= max_participation else "exceeds_participation_limit",
            "max_participation": max_participation, "execution_enabled": False,
            "model": "square_root_impact_heuristic_not_calibrated"}


def model_validation(reference, current, reference_accuracy, current_accuracy, psi_threshold=0.2, max_accuracy_drop=0.05):
    a, b = _finite(reference, "reference", 2), _finite(current, "current", 2)
    if not all(math.isfinite(float(x)) for x in [reference_accuracy, current_accuracy, psi_threshold, max_accuracy_drop]):
        raise ValueError("model validation inputs must be finite")
    if not 0 <= reference_accuracy <= 1 or not 0 <= current_accuracy <= 1 or psi_threshold <= 0 or not 0 <= max_accuracy_drop <= 1:
        raise ValueError("invalid accuracy or threshold")
    lo, hi = min(a), max(a)
    if hi == lo:
        psi = 0.0 if all(x == lo for x in b) else 13.815510557964274
    else:
        edges = [lo + (hi - lo) * i / 10 for i in range(11)]
        def counts(xs):
            c = [0] * 10
            for x in xs:
                idx = min(9, max(0, next((i for i in range(10) if x <= edges[i + 1]), 9)))
                c[idx] += 1
            return [max(v / len(xs), 1e-6) for v in c]
        p, q = counts(a), counts(b)
        psi = sum((y - x) * math.log(y / x) for x, y in zip(p, q))
    drop = reference_accuracy - current_accuracy
    alerts = []
    if psi > psi_threshold: alerts.append("feature_distribution_drift")
    if drop > max_accuracy_drop: alerts.append("performance_degradation")
    return {"psi": psi, "accuracy_drop": drop, "alerts": alerts,
            "deployment_authorized": False, "status": "review_required" if alerts else "no_threshold_breach_detected",
            "note": "Threshold checks do not authorize deployment; evaluate labels, calibration and untouched OOS data."}


def macro_intelligence(inflation_change, rates_change, growth_change, volatility_change):
    vals = [float(x) for x in [inflation_change, rates_change, growth_change, volatility_change]]
    if not all(math.isfinite(x) for x in vals):
        raise ValueError("macro inputs must be finite")
    inflation, rates, growth, vol = vals
    if vol < 0:
        raise ValueError("volatility_change cannot be negative")
    if growth < 0 and inflation > 0: regime = "stagflationary_pressure"
    elif growth < 0: regime = "growth_contraction"
    elif inflation > 0 and rates > 0: regime = "inflation_and_tightening"
    elif growth > 0 and inflation <= 0: regime = "disinflationary_growth"
    else: regime = "mixed_or_transition"
    return {"regime": regime, "inputs": {"inflation_change": inflation, "rates_change": rates,
            "growth_change": growth, "volatility_change": vol},
            "confidence": "not_estimated", "source_data_connected": False,
            "interpretation": "Rule-based label only; inputs require timestamped, point-in-time macro data."}


def rank_strategies(candidates, multiple_testing_penalty=0.01):
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("candidates must be a non-empty list")
    if not math.isfinite(float(multiple_testing_penalty)) or multiple_testing_penalty < 0:
        raise ValueError("multiple_testing_penalty must be finite and non-negative")
    out = []
    for item in candidates:
        name = str(item.get("name", "")).strip()
        sharpe, dd, turnover = (float(item.get(k, 0)) for k in ("oos_sharpe", "max_drawdown", "turnover"))
        trials = int(item.get("trials_tested", 1))
        if not name or not all(math.isfinite(x) for x in [sharpe, dd, turnover]) or dd > 0 or turnover < 0 or trials < 1:
            raise ValueError("invalid strategy candidate fields")
        penalty = multiple_testing_penalty * math.log1p(trials - 1)
        score = sharpe + dd - 0.001 * turnover - penalty
        out.append({"name": name, "score": score, "multiple_testing_penalty": penalty,
                    "oos_sharpe_input": sharpe, "max_drawdown_input": dd, "turnover_input": turnover})
    out.sort(key=lambda x: x["score"], reverse=True)
    return {"ranked_candidates": out, "ranking_method": "oos_sharpe_plus_drawdown_minus_turnover_and_trial_penalty",
            "validated_alpha": False, "recommendation": "research_triage_only"}
