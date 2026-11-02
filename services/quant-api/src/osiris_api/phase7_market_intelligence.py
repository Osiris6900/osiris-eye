"""Phase 7 market and alpha research diagnostics. Offline/research only."""
from __future__ import annotations

import math
from statistics import mean, median, pstdev


def _series(values, label, minimum=2):
    if not isinstance(values, list) or len(values) < minimum:
        raise ValueError(f"{label} must contain at least {minimum} observations")
    result = [float(v) for v in values]
    if not all(math.isfinite(v) for v in result):
        raise ValueError(f"{label} must be finite")
    return result


def cross_asset_alpha(returns, max_lag=3):
    if not isinstance(returns, dict) or len(returns) < 2:
        raise ValueError("returns must contain at least two assets")
    data = {str(k): _series(v, f"returns[{k}]", 4) for k, v in returns.items()}
    if len({len(v) for v in data.values()}) != 1:
        raise ValueError("all return series must be aligned and equal length")
    if not isinstance(max_lag, int) or not 0 <= max_lag < min(map(len, data.values())) - 2:
        raise ValueError("max_lag is outside supported range")
    pairs = []
    for a in sorted(data):
        for b in sorted(data):
            if a >= b:
                continue
            for lag in range(-max_lag, max_lag + 1):
                x, y = data[a], data[b]
                if lag > 0: x, y = x[:-lag], y[lag:]
                elif lag < 0: x, y = x[-lag:], y[:lag]
                if len(x) < 3 or pstdev(x) == 0 or pstdev(y) == 0:
                    corr = None
                else:
                    mx, my = mean(x), mean(y)
                    corr = sum((u-mx)*(v-my) for u,v in zip(x,y)) / math.sqrt(sum((u-mx)**2 for u in x)*sum((v-my)**2 for v in y))
                pairs.append({"asset_x": a, "asset_y": b, "lag": lag, "correlation": corr})
    return {"relationships": pairs, "hypotheses_only": True,
            "warning": "Correlation and lead-lag scans are exploratory; control for multiple testing, common factors and publication-time alignment."}


def options_flow_intelligence(trades):
    if not isinstance(trades, list) or not trades:
        raise ValueError("trades must be a non-empty list")
    totals = {"call_premium": 0.0, "put_premium": 0.0, "call_volume": 0.0, "put_volume": 0.0}
    unusual = []
    for i, t in enumerate(trades):
        side, kind = str(t.get("side", "")).lower(), str(t.get("option_type", "")).lower()
        premium, volume = float(t.get("premium", 0)), float(t.get("volume", 0))
        if side not in {"buy", "sell"} or kind not in {"call", "put"} or not math.isfinite(premium) or not math.isfinite(volume) or premium < 0 or volume < 0:
            raise ValueError(f"invalid option trade at index {i}")
        signed = premium if side == "buy" else -premium
        totals[f"{kind}_premium"] += signed
        totals[f"{kind}_volume"] += volume
        if t.get("unusual_flag") is True: unusual.append({"index": i, "symbol": str(t.get("symbol", "")), "option_type": kind})
    gross = abs(totals["call_premium"]) + abs(totals["put_premium"])
    return {**totals, "net_call_put_premium_ratio": totals["call_premium"] / totals["put_premium"] if totals["put_premium"] else None,
            "unusual_activity_flags": unusual, "trade_count": len(trades),
            "source_data_connected": False, "interpretation": "Flow is not inherently directional; trade classification and venue coverage must be independently validated."}


def liquidity_forecast(spread_bps, volume, volatility, horizon=1):
    spread = _series(spread_bps, "spread_bps")
    vol = _series(volatility, "volatility")
    volume = _series(volume, "volume")
    if len({len(spread), len(vol), len(volume)}) != 1:
        raise ValueError("spread, volume and volatility arrays must align")
    if any(x < 0 for x in spread + vol + volume) or not isinstance(horizon, int) or horizon < 1:
        raise ValueError("liquidity inputs and horizon must be non-negative/positive")
    recent = min(5, len(spread))
    spread_est = mean(spread[-recent:])
    vol_est = mean(vol[-recent:])
    volume_est = mean(volume[-recent:])
    score = spread_est * (1 + vol_est) / max(math.sqrt(volume_est), 1e-9)
    return {"horizon_observations": horizon, "recent_mean_spread_bps": spread_est,
            "recent_mean_volume": volume_est, "recent_mean_volatility": vol_est,
            "relative_liquidity_stress_score": score, "forecast_method": "recent_window_heuristic",
            "forecast_calibrated": False, "source_data_connected": False}


def detect_market_anomalies(returns, volumes, spread_bps, z_threshold=4.0):
    r, v, s = _series(returns, "returns", 4), _series(volumes, "volumes", 4), _series(spread_bps, "spread_bps", 4)
    if len({len(r), len(v), len(s)}) != 1 or not math.isfinite(float(z_threshold)) or z_threshold <= 0:
        raise ValueError("aligned series and positive finite z_threshold required")
    def robust_z(xs, value):
        m = median(xs)
        mad = median([abs(x-m) for x in xs])
        return (value-m) / (1.4826*mad) if mad else (0.0 if value == m else math.copysign(float(len(xs)), value-m))
    flags = []
    for i in range(1, len(r)):
        scores = {"return": robust_z(r[:-1], r[i]), "volume": robust_z(v[:-1], v[i]), "spread_bps": robust_z(s[:-1], s[i])}
        flagged = [k for k, z in scores.items() if abs(z) >= z_threshold]
        if flagged: flags.append({"index": i, "signals": flagged, "robust_z_scores": scores})
    return {"anomalies": flags, "threshold": z_threshold, "observations_scanned": len(r)-1,
            "alert_only": True, "automatic_trading_action": False}


def offline_rl_evaluation(episodes, max_importance_weight=10.0):
    if not isinstance(episodes, list) or not episodes:
        raise ValueError("episodes must be a non-empty list")
    if not math.isfinite(float(max_importance_weight)) or max_importance_weight <= 0:
        raise ValueError("max_importance_weight must be positive and finite")
    returns, weights = [], []
    for i, e in enumerate(episodes):
        reward = float(e.get("reward", float("nan")))
        behavior = float(e.get("behavior_probability", float("nan")))
        target = float(e.get("target_probability", float("nan")))
        if not all(math.isfinite(x) for x in [reward, behavior, target]) or not 0 < behavior <= 1 or not 0 <= target <= 1:
            raise ValueError(f"invalid episode {i}")
        returns.append(reward)
        weights.append(min(target / behavior, max_importance_weight))
    weighted = sum(r*w for r,w in zip(returns, weights)) / len(returns)
    return {"episode_count": len(episodes), "mean_observed_reward": mean(returns),
            "clipped_importance_sampling_estimate": weighted,
            "effective_sample_size": (sum(weights)**2 / sum(w*w for w in weights)) if any(weights) else 0.0,
            "offline_only": True, "policy_training_performed": False,
            "warning": "Importance sampling relies on logged-policy support and correct propensities; this is not deployment approval."}


def trade_surveillance(events, cancel_ratio_threshold=0.8, burst_window_seconds=1.0):
    if not isinstance(events, list) or not events:
        raise ValueError("events must be a non-empty list")
    if not 0 < cancel_ratio_threshold <= 1 or burst_window_seconds <= 0:
        raise ValueError("invalid surveillance thresholds")
    by_order = {}
    flags = []
    for i, e in enumerate(events):
        order_id = str(e.get("order_id", "")).strip()
        action = str(e.get("action", "")).lower()
        ts = float(e.get("timestamp_seconds", float("nan")))
        qty = float(e.get("quantity", 0))
        if not order_id or action not in {"submit", "modify", "cancel", "fill"} or not all(math.isfinite(x) for x in [ts, qty]) or qty < 0:
            raise ValueError(f"invalid surveillance event at index {i}")
        by_order.setdefault(order_id, []).append((ts, action, qty))
    for oid, rows in by_order.items():
        cancels = sum(a == "cancel" for _,a,_ in rows)
        submits = sum(a == "submit" for _,a,_ in rows)
        ratio = cancels / max(submits, 1)
        if submits >= 3 and ratio >= cancel_ratio_threshold:
            flags.append({"order_id": oid, "indicator": "high_cancel_to_submit_ratio", "ratio": ratio})
        times = sorted(t for t,_,_ in rows)
        if len(times) >= 3 and any(times[j+2]-times[j] <= burst_window_seconds for j in range(len(times)-2)):
            flags.append({"order_id": oid, "indicator": "rapid_order_event_burst"})
    return {"alerts": flags, "orders_reviewed": len(by_order), "events_reviewed": len(events),
            "finding_type": "review_indicators_not_misconduct_determinations",
            "human_review_required": True, "enforcement_action_taken": False}
