"""Deterministic, data-in/data-out foundations for the ten Osiris Eye platform modules.

These functions are research primitives, not trading signals or broker instructions.
All inputs must be point-in-time and sourced from authorized data providers.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil, isfinite, sqrt
from statistics import mean
from typing import Iterable, Sequence


def _finite(values: Iterable[float], name: str) -> list[float]:
    result = [float(value) for value in values]
    if not result or not all(isfinite(value) for value in result):
        raise ValueError(f"{name} must contain finite values")
    return result


@dataclass(frozen=True)
class AlternativeEvent:
    source: str
    event_time: str
    published_at: str
    category: str
    sentiment: float

    def validate(self) -> "AlternativeEvent":
        if not all((self.source.strip(), self.event_time.strip(),
                    self.published_at.strip(), self.category.strip())):
            raise ValueError("event metadata must be non-empty")
        if not isfinite(self.sentiment) or not -1.0 <= self.sentiment <= 1.0:
            raise ValueError("sentiment must be finite and between -1 and 1")
        return self


def order_flow_imbalance(bid_volume: float, ask_volume: float) -> float:
    """Normalized displayed-volume imbalance in [-1, 1]."""
    if not all(isfinite(v) and v >= 0 for v in (bid_volume, ask_volume)):
        raise ValueError("volumes must be finite and non-negative")
    total = bid_volume + ask_volume
    return 0.0 if total == 0 else (bid_volume - ask_volume) / total


def inverse_volatility_weights(volatilities: Sequence[float]) -> list[float]:
    """Long-only normalized inverse-volatility weights; not an optimizer."""
    vols = _finite(volatilities, "volatilities")
    if any(v <= 0 for v in vols):
        raise ValueError("volatilities must be positive")
    inverse = [1.0 / v for v in vols]
    total = sum(inverse)
    return [v / total for v in inverse]


def historical_var_es(returns: Sequence[float], confidence: float = 0.95) -> tuple[float, float]:
    """Nearest-rank historical loss VaR and tail-mean ES, as positive fractions.

    VaR uses rank ceil(confidence * n), with ranks counted from one. Expected
    shortfall is the arithmetic mean of observed losses at or beyond that VaR.
    This finite-sample convention is explicit and intentionally does not
    interpolate between observations.
    """
    values = _finite(returns, "returns")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1")
    losses = sorted(-v for v in values)
    rank = max(1, ceil(confidence * len(losses)))
    var = max(0.0, losses[rank - 1])
    tail = [loss for loss in losses if loss >= var]
    return var, mean(tail) if tail else var


def population_stability_index(reference: Sequence[float], current: Sequence[float],
                               bins: int = 10, epsilon: float = 1e-6) -> float:
    """PSI for numeric samples using reference quantile cut points."""
    ref = sorted(_finite(reference, "reference"))
    cur = _finite(current, "current")
    if bins < 2 or epsilon <= 0:
        raise ValueError("bins must be >= 2 and epsilon must be positive")
    cuts = [ref[min(len(ref) - 1, int(i * len(ref) / bins))] for i in range(1, bins)]
    def proportions(sample: Sequence[float]) -> list[float]:
        counts = [0] * bins
        for value in sample:
            bucket = sum(value > cut for cut in cuts)
            counts[min(bins - 1, bucket)] += 1
        return [max(epsilon, count / len(sample)) for count in counts]
    p, q = proportions(ref), proportions(cur)
    return sum((b - a) * __import__("math").log(b / a) for a, b in zip(p, q))


def strategy_summary(returns: Sequence[float], periods_per_year: int = 252) -> dict[str, float]:
    """Basic return diagnostics; assumes equally spaced periodic returns."""
    values = _finite(returns, "returns")
    if periods_per_year <= 0 or any(v <= -1 for v in values):
        raise ValueError("invalid annualization or return below -100%")
    equity, peak, max_drawdown = 1.0, 1.0, 0.0
    for value in values:
        equity *= 1.0 + value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak)
    avg = mean(values)
    variance = sum((v - avg) ** 2 for v in values) / len(values)
    volatility = sqrt(variance)
    sharpe = 0.0 if volatility == 0 else avg / volatility * sqrt(periods_per_year)
    return {"total_return": equity - 1.0, "annualized_sharpe": sharpe,
            "max_drawdown": max_drawdown, "period_volatility": volatility}


def estimated_execution_cost_bps(half_spread_bps: float, slippage_bps: float,
                                 impact_bps: float = 0.0) -> float:
    costs = (half_spread_bps, slippage_bps, impact_bps)
    if not all(isfinite(v) and v >= 0 for v in costs):
        raise ValueError("execution costs must be finite and non-negative")
    return sum(costs)


def macro_regime(realized_volatility: float, inflation_change: float,
                 rates_change: float, vol_threshold: float = 0.30) -> str:
    """Transparent heuristic label; thresholds require market-specific calibration."""
    values = (realized_volatility, inflation_change, rates_change, vol_threshold)
    if not all(isfinite(v) for v in values) or realized_volatility < 0 or vol_threshold <= 0:
        raise ValueError("invalid macro regime inputs")
    if realized_volatility >= vol_threshold:
        return "high_volatility"
    if inflation_change > 0 and rates_change > 0:
        return "tightening_pressure"
    if inflation_change < 0 and rates_change < 0:
        return "easing_pressure"
    return "mixed"


@dataclass(frozen=True)
class HealthCheck:
    component: str
    healthy: bool
    detail: str


def system_health(checks: Sequence[HealthCheck]) -> dict[str, object]:
    if not checks:
        raise ValueError("at least one health check is required")
    return {"healthy": all(c.healthy for c in checks),
            "components": [{"component": c.component, "healthy": c.healthy,
                            "detail": c.detail} for c in checks]}


@dataclass(frozen=True)
class AgentTask:
    role: str
    objective: str
    requires_human_approval: bool = True

    def validate(self) -> "AgentTask":
        allowed = {"data_research", "strategy_research", "risk_review",
                   "code_review", "reporting"}
        if self.role not in allowed or not self.objective.strip():
            raise ValueError("unsupported agent role or empty objective")
        if not self.requires_human_approval:
            raise ValueError("agent tasks must require human approval")
        return self
