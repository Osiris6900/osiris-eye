"""Mesh-native market intelligence: consensus prices, relationships, regimes.

Three layers that make the network its own source of market truth instead of
depending on any single exchange or vendor:

1. **Consensus view** — many observers and many keyless sources report the
   same instrument; the mesh accepts a median price only when a quorum of
   independent providers agrees within a deviation bound. One source dying
   or lying does not move the network's view. Fail closed otherwise.
2. **Relationship research** — returns, correlation matrix and lead-lag
   scans across everything the mesh stores, so the network itself sees
   what moves with what, and who moves first.
3. **Regime + move hypotheses** — trend/volatility regime labels and
   structured hypotheses for "why is it moving": magnitude, breadth,
   leading instruments. Hypotheses are labeled as such; causality is never
   claimed. Live trading remains disabled.

Pure stdlib; no network access; operates on stored observation payloads.
"""
from __future__ import annotations

import json
import math
from typing import Any, Iterable

MIN_QUORUM_OBSERVERS = 3
MIN_QUORUM_SOURCES = 2
DEFAULT_MAX_DEVIATION_PCT = 1.0
MAX_SERIES_LENGTH = 10_000


# ---------------------------------------------------------------- consensus

def quote_value(stored_payload: dict[str, Any]) -> float:
    """Extract one numeric quote from a stored observation payload.

    Recognizes the global_market_watch shapes: crypto ``last``, index
    ``close`` and FX ``rate_per_usd``. Fail closed on anything else.
    """
    if not isinstance(stored_payload, dict):
        raise ValueError("invalid_stored_payload")
    inner = stored_payload.get("payload")
    if not isinstance(inner, str):
        raise ValueError("invalid_stored_payload")
    try:
        quote = json.loads(inner)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_stored_payload") from exc
    if not isinstance(quote, dict):
        raise ValueError("invalid_stored_payload")
    for key in ("last", "close", "rate_per_usd"):
        if key in quote and quote[key] is not None:
            try:
                value = float(quote[key])
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("invalid_quote_value") from exc
            if not math.isfinite(value) or value <= 0:
                raise ValueError("invalid_quote_value")
            return value
    raise ValueError("no_recognized_quote_field")


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def consensus_price(
    reports: Iterable[dict[str, Any]],
    *,
    min_observers: int = MIN_QUORUM_OBSERVERS,
    min_sources: int = MIN_QUORUM_SOURCES,
    max_deviation_pct: float = DEFAULT_MAX_DEVIATION_PCT,
) -> dict[str, Any]:
    """Form the mesh's own price view from independent observer reports.

    ``reports`` items: ``{"observer": <peer id>, "source": <provider>,
    "value": <float>}``. Acceptance requires both a quorum of observers and
    a quorum of distinct sources agreeing within ``max_deviation_pct`` of
    the median — so no single provider or peer can dictate the price. The
    accepted price is the median of the *agreeing* reports only.
    """
    if min_observers < 1 or min_sources < 1 or max_deviation_pct <= 0:
        raise ValueError("invalid_consensus_bounds")
    values: list[float] = []
    sources: dict[str, int] = {}
    for report in reports:
        try:
            value = float(report["value"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("invalid_consensus_report") from exc
        if not math.isfinite(value) or value <= 0:
            raise ValueError("invalid_consensus_report")
        source = str(report.get("source", "")).strip()
        if not source:
            raise ValueError("invalid_consensus_report")
        values.append(value)
        sources[source] = sources.get(source, 0) + 1

    if len(values) < min_observers:
        return {"accepted": False, "reason": "observer_quorum_not_met",
                "observers": len(values), "sources": len(sources)}
    if len(sources) < min_sources:
        return {"accepted": False, "reason": "source_quorum_not_met",
                "observers": len(values), "sources": len(sources)}

    med = _median(values)
    inliers = [v for v in values if abs(v - med) / med * 100.0 <= max_deviation_pct]
    if len(inliers) < min_observers:
        return {"accepted": False, "reason": "consensus_disagreement",
                "observers": len(values), "sources": len(sources),
                "agreeing": len(inliers)}
    return {
        "accepted": True,
        "price": _median(inliers),
        "observers": len(values),
        "sources": len(sources),
        "agreeing": len(inliers),
        "agreement_ratio": round(len(inliers) / len(values), 4),
        "outliers_rejected": len(values) - len(inliers),
        "max_deviation_pct": max_deviation_pct,
        "live_execution": False,
    }


MIN_SOURCE_WEIGHT = 0.5
MAX_SOURCE_WEIGHT = 2.0


def _weighted_median(pairs: list[tuple[float, float]]) -> float:
    """Weighted median of (value, weight) pairs: the value where cumulative
    weight first reaches half the total. Deterministic; weights must be
    positive and finite."""
    ordered = sorted(pairs, key=lambda item: item[0])
    total = sum(weight for _, weight in ordered)
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= total / 2.0:
            return value
    return ordered[-1][0]


def source_weights_from_reports(
    reports: Iterable[dict[str, Any]],
    *,
    min_weight: float = MIN_SOURCE_WEIGHT,
    max_weight: float = MAX_SOURCE_WEIGHT,
    scale_pct: float = 0.5,
    floor_pct: float = 0.05,
) -> dict[str, float]:
    """Health weight per source from its recent agreement with the mesh.

    ``reports`` items: ``{"source": <provider>, "value": <float>}`` from a
    recent window. Each source's weight is ``scale_pct`` divided by its mean
    absolute deviation (%) from the overall median, clamped to
    [``min_weight``, ``max_weight``]: sources that consistently land on the
    mesh median rise toward 2.0, chronic outliers sink toward 0.5. This is
    a bounded, transparent heuristic — never an authority grant: the quorum
    and inlier gates below still apply unchanged.
    """
    if not 0 < min_weight <= 1 <= max_weight or scale_pct <= 0 or floor_pct <= 0:
        raise ValueError("invalid_weight_bounds")
    per_source: dict[str, list[float]] = {}
    values: list[float] = []
    for report in reports:
        try:
            value = float(report["value"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("invalid_consensus_report") from exc
        if not math.isfinite(value) or value <= 0:
            raise ValueError("invalid_consensus_report")
        source = str(report.get("source", "")).strip()
        if not source:
            raise ValueError("invalid_consensus_report")
        per_source.setdefault(source, []).append(value)
        values.append(value)
    if not values:
        raise ValueError("weights_require_reports")

    med = _median(values)
    weights: dict[str, float] = {}
    for source in sorted(per_source):
        deviations = [abs(v - med) / med * 100.0 for v in per_source[source]]
        mean_dev = sum(deviations) / len(deviations)
        weight = scale_pct / max(mean_dev, floor_pct)
        weights[source] = round(min(max(weight, min_weight), max_weight), 4)
    return weights


def weighted_consensus_price(
    reports: Iterable[dict[str, Any]],
    *,
    weights: dict[str, float] | None = None,
    min_observers: int = MIN_QUORUM_OBSERVERS,
    min_sources: int = MIN_QUORUM_SOURCES,
    max_deviation_pct: float = DEFAULT_MAX_DEVIATION_PCT,
) -> dict[str, Any]:
    """Consensus price with per-source health weights.

    Same quorum and inlier gates as :func:`consensus_price`; the accepted
    price is the *weighted* median of the agreeing inliers, where each
    report's weight comes from ``weights[source]`` clamped to
    [MIN_SOURCE_WEIGHT, MAX_SOURCE_WEIGHT] (default 1.0). With
    ``weights=None`` this defers to :func:`consensus_price` unchanged, so
    behaviour only differs when real health weights are supplied. The
    weights used are reported back verbatim for transparency.
    """
    if weights is None:
        return consensus_price(
            reports, min_observers=min_observers, min_sources=min_sources,
            max_deviation_pct=max_deviation_pct,
        )
    if min_observers < 1 or min_sources < 1 or max_deviation_pct <= 0:
        raise ValueError("invalid_consensus_bounds")
    entries: list[tuple[float, float, str]] = []
    sources: dict[str, int] = {}
    for report in reports:
        try:
            value = float(report["value"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("invalid_consensus_report") from exc
        if not math.isfinite(value) or value <= 0:
            raise ValueError("invalid_consensus_report")
        source = str(report.get("source", "")).strip()
        if not source:
            raise ValueError("invalid_consensus_report")
        try:
            raw_weight = float(weights.get(source, 1.0))
        except (TypeError, ValueError, OverflowError):
            raw_weight = 1.0
        if not math.isfinite(raw_weight):
            raw_weight = 1.0
        weight = min(max(raw_weight, MIN_SOURCE_WEIGHT), MAX_SOURCE_WEIGHT)
        entries.append((value, weight, source))
        sources[source] = sources.get(source, 0) + 1

    if len(entries) < min_observers:
        return {"accepted": False, "reason": "observer_quorum_not_met",
                "observers": len(entries), "sources": len(sources)}
    if len(sources) < min_sources:
        return {"accepted": False, "reason": "source_quorum_not_met",
                "observers": len(entries), "sources": len(sources)}

    med = _median([value for value, _, _ in entries])
    inliers = [(value, weight, source) for value, weight, source in entries
               if abs(value - med) / med * 100.0 <= max_deviation_pct]
    if len(inliers) < min_observers:
        return {"accepted": False, "reason": "consensus_disagreement",
                "observers": len(entries), "sources": len(sources),
                "agreeing": len(inliers)}
    weights_used: dict[str, float] = {}
    for source in sorted(sources):
        try:
            raw = float(weights.get(source, 1.0))
        except (TypeError, ValueError, OverflowError):
            raw = 1.0
        if not math.isfinite(raw):
            raw = 1.0
        weights_used[source] = round(min(max(raw, MIN_SOURCE_WEIGHT), MAX_SOURCE_WEIGHT), 4)
    return {
        "accepted": True,
        "price": _weighted_median([(value, weight) for value, weight, _ in inliers]),
        "method": "weighted_median_of_inliers",
        "observers": len(entries),
        "sources": len(sources),
        "agreeing": len(inliers),
        "agreement_ratio": round(len(inliers) / len(entries), 4),
        "outliers_rejected": len(entries) - len(inliers),
        "max_deviation_pct": max_deviation_pct,
        "weights_used": weights_used,
        "live_execution": False,
    }


# ---------------------------------------------------------------- research

def returns_series(values: list[float]) -> list[float]:
    """Simple period returns from an oldest-to-newest price series."""
    if len(values) > MAX_SERIES_LENGTH:
        raise ValueError("series_too_long")
    if len(values) < 2:
        return []
    if any(not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError("invalid_price_series")
    return [(values[i] - values[i - 1]) / values[i - 1] for i in range(1, len(values))]


def pearson(x: list[float], y: list[float]) -> float:
    if len(x) != len(y) or len(x) < 2:
        raise ValueError("correlation_requires_equal_series_of_two_or_more")
    if any(not math.isfinite(v) for v in x + y):
        raise ValueError("non_finite_value")
    mean_x, mean_y = sum(x) / len(x), sum(y) / len(y)
    cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y))
    var_x = sum((a - mean_x) ** 2 for a in x)
    var_y = sum((b - mean_y) ** 2 for b in y)
    if var_x == 0 or var_y == 0:
        raise ValueError("zero_variance_series")
    return cov / math.sqrt(var_x * var_y)


def correlation_matrix(returns_map: dict[str, list[float]]) -> dict[str, dict[str, float]]:
    """Pairwise Pearson correlation over aligned trailing windows."""
    if not returns_map:
        raise ValueError("empty_returns_map")
    matrix: dict[str, dict[str, float]] = {}
    for name in sorted(returns_map):
        matrix[name] = {}
        for other in sorted(returns_map):
            a, b = returns_map[name], returns_map[other]
            overlap = min(len(a), len(b))
            if overlap < 2:
                matrix[name][other] = 0.0
                continue
            try:
                matrix[name][other] = round(pearson(a[-overlap:], b[-overlap:]), 4)
            except ValueError:
                matrix[name][other] = 0.0
    return matrix


def lead_lag_scan(x: list[float], y: list[float], *, max_lag: int = 5) -> dict[str, Any]:
    """Find the lag where ``x`` and ``y`` co-move most strongly.

    Positive lag means ``x`` leads ``y`` by that many periods; negative
    means ``y`` leads. A research hypothesis, not causality.
    """
    if max_lag < 0 or max_lag > 30:
        raise ValueError("invalid_max_lag")
    best = {"lag": 0, "correlation": 0.0}
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            a, b = x[:len(x) - lag or None], y[lag:]
        else:
            a, b = x[-lag:], y[:len(y) + lag or None]
        overlap = min(len(a), len(b))
        if overlap < 3:
            continue
        try:
            corr = pearson(a[-overlap:], b[-overlap:])
        except ValueError:
            continue
        if abs(corr) > abs(best["correlation"]):
            best = {"lag": lag, "correlation": round(corr, 4)}
    direction = "x_leads" if best["lag"] > 0 else "y_leads" if best["lag"] < 0 else "synchronous"
    return {**best, "direction": direction, "causality": "not_established"}


def regime_label(returns: list[float], *, window: int = 20) -> dict[str, Any]:
    """Label the recent trend and volatility regime of a returns series."""
    if window < 2 or len(returns) < window * 2:
        raise ValueError("regime_requires_at_least_two_windows")
    if any(not math.isfinite(v) for v in returns):
        raise ValueError("non_finite_value")
    recent = returns[-window:]
    baseline = returns[:-window]
    mean_recent = sum(recent) / window
    var_baseline = sum((r - sum(baseline) / len(baseline)) ** 2 for r in baseline) / (len(baseline) - 1)
    std_baseline = math.sqrt(var_baseline) if var_baseline > 0 else 0.0
    var_recent = sum((r - mean_recent) ** 2 for r in recent) / (window - 1)
    std_recent = math.sqrt(var_recent)

    trend_strength = mean_recent / std_baseline if std_baseline > 0 else 0.0
    if trend_strength > 0.5:
        trend = "up"
    elif trend_strength < -0.5:
        trend = "down"
    else:
        trend = "flat"
    vol_ratio = std_recent / std_baseline if std_baseline > 0 else 1.0
    volatility = "high" if vol_ratio > 1.5 else "low" if vol_ratio < 0.67 else "normal"
    return {
        "trend": trend,
        "volatility": volatility,
        "trend_strength": round(trend_strength, 4),
        "volatility_ratio": round(vol_ratio, 4),
        "window": window,
    }


def explain_move(
    target_returns: list[float],
    context_returns: dict[str, list[float]],
    *,
    window: int = 20,
    max_lag: int = 5,
    leader_threshold: float = 0.5,
) -> dict[str, Any]:
    """Structured hypothesis for the target's latest move.

    Reports magnitude (z-score vs baseline), direction, market breadth
    (how many context instruments moved the same way) and which context
    instruments most often led the target. This is pattern evidence for
    research; it never establishes why the market moved.
    """
    if len(target_returns) < window * 2:
        raise ValueError("explain_move_requires_at_least_two_windows")
    latest = target_returns[-1]
    baseline = target_returns[-window * 2:-1]
    mean_b = sum(baseline) / len(baseline)
    var_b = sum((r - mean_b) ** 2 for r in baseline) / (len(baseline) - 1)
    std_b = math.sqrt(var_b)
    z_score = (latest - mean_b) / std_b if std_b > 0 else 0.0
    direction = "up" if latest > 0 else "down" if latest < 0 else "flat"

    same_direction = 0
    compared = 0
    leaders = []
    for name in sorted(context_returns):
        series = context_returns[name]
        if not series:
            continue
        compared += 1
        if (series[-1] > 0) == (latest > 0):
            same_direction += 1
        scan = lead_lag_scan(series, target_returns, max_lag=max_lag)
        if scan["lag"] > 0 and scan["correlation"] >= leader_threshold:
            leaders.append({"instrument": name, "lag": scan["lag"],
                            "correlation": scan["correlation"]})
    leaders.sort(key=lambda item: -item["correlation"])

    return {
        "hypothesis": True,
        "causality": "not_established",
        "latest_return": round(latest, 8),
        "direction": direction,
        "magnitude_z_score": round(z_score, 4),
        "unusual_move": abs(z_score) >= 2.0,
        "breadth_same_direction": same_direction,
        "breadth_compared": compared,
        "breadth_ratio": round(same_direction / compared, 4) if compared else None,
        "leading_instruments": leaders[:3],
        "regime": regime_label(target_returns, window=window),
        "live_execution": False,
    }
