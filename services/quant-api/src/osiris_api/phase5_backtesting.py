"""Institutional-style portfolio backtest core with explicit lag and cost assumptions.

This is a deterministic research simulator, not a fill-level execution simulator.
"""
from __future__ import annotations

from math import isfinite, sqrt
from statistics import mean, pstdev
from typing import Any


def run_portfolio_backtest(
    asset_returns: list[dict[str, float]],
    target_weights: list[dict[str, float]],
    *,
    commission_bps: float = 0.0,
    spread_bps: float = 0.0,
    slippage_bps: float = 0.0,
    impact_bps: float = 0.0,
    periods_per_year: int = 252,
    max_gross_leverage: float = 1.0,
    initial_capital: float = 1_000_000.0,
) -> dict[str, Any]:
    """Apply target_weights[t-1] to asset_returns[t]; weights are signed fractions."""
    if len(asset_returns) != len(target_weights) or len(asset_returns) < 3:
        raise ValueError("asset_returns and target_weights must have equal length >= 3")
    if not isinstance(periods_per_year, int) or periods_per_year < 1:
        raise ValueError("periods_per_year must be a positive integer")
    scalars = [commission_bps, spread_bps, slippage_bps, impact_bps,
               max_gross_leverage, initial_capital]
    if not all(isfinite(float(x)) for x in scalars):
        raise ValueError("costs, leverage and capital must be finite")
    if any(x < 0 for x in scalars[:5]) or initial_capital <= 0:
        raise ValueError("costs and leverage must be non-negative; capital must be positive")

    assets = set(asset_returns[0])
    if not assets or any(set(row) != assets for row in asset_returns):
        raise ValueError("every return row must contain the same non-empty asset universe")
    if any(set(row) != assets for row in target_weights):
        raise ValueError("every weight row must match the return asset universe")
    clean_returns = []
    clean_weights = []
    for row in asset_returns:
        values = {k: float(v) for k, v in row.items()}
        if not all(isfinite(v) and v > -1 for v in values.values()):
            raise ValueError("asset returns must be finite and greater than -100%")
        clean_returns.append(values)
    for row in target_weights:
        values = {k: float(v) for k, v in row.items()}
        if not all(isfinite(v) for v in values.values()):
            raise ValueError("target weights must be finite")
        gross = sum(abs(v) for v in values.values())
        if gross > max_gross_leverage + 1e-9:
            raise ValueError("target gross exposure exceeds max_gross_leverage")
        clean_weights.append(values)

    all_in_bps = commission_bps + spread_bps + slippage_bps + impact_bps
    portfolio_returns: list[float] = []
    turnovers: list[float] = []
    costs: list[float] = []
    previous = {asset: 0.0 for asset in assets}
    for t in range(1, len(clean_returns)):
        weights = clean_weights[t - 1]
        turnover = sum(abs(weights[a] - previous[a]) for a in assets)
        cost = turnover * all_in_bps / 10_000.0
        gross_return = sum(weights[a] * clean_returns[t][a] for a in assets)
        net_return = gross_return - cost
        if not isfinite(net_return) or net_return <= -1:
            raise ValueError("net portfolio return must be finite and greater than -100%")
        portfolio_returns.append(net_return)
        turnovers.append(turnover)
        costs.append(cost)
        previous = weights

    equity_curve = [initial_capital]
    peak = initial_capital
    max_drawdown = 0.0
    for ret in portfolio_returns:
        equity_curve.append(equity_curve[-1] * (1.0 + ret))
        peak = max(peak, equity_curve[-1])
        max_drawdown = max(max_drawdown, (peak - equity_curve[-1]) / peak)
    years = len(portfolio_returns) / periods_per_year
    total_return = equity_curve[-1] / initial_capital - 1.0
    cagr = (equity_curve[-1] / initial_capital) ** (1.0 / years) - 1.0 if years else 0.0
    volatility = pstdev(portfolio_returns) * sqrt(periods_per_year)
    avg_return = mean(portfolio_returns)
    sharpe = (avg_return / pstdev(portfolio_returns) * sqrt(periods_per_year)
              if pstdev(portfolio_returns) > 0 else 0.0)
    return {
        "periods": len(portfolio_returns),
        "assets": sorted(assets),
        "signal_alignment": "target_weights[t-1]_applied_to_returns[t]",
        "portfolio_returns": portfolio_returns,
        "equity_curve": equity_curve,
        "initial_capital": initial_capital,
        "ending_equity": equity_curve[-1],
        "total_return": total_return,
        "cagr": cagr,
        "annualized_volatility": volatility,
        "sharpe_ratio": sharpe,
        "max_drawdown": max_drawdown,
        "total_turnover": sum(turnovers),
        "average_turnover": mean(turnovers),
        "total_cost_return_fraction": sum(costs),
        "cost_assumptions_bps": {
            "commission": commission_bps, "spread": spread_bps,
            "slippage": slippage_bps, "impact": impact_bps, "all_in": all_in_bps,
        },
        "execution": "not_fill_simulated",
        "limitations": [
            "No market impact calibration, partial fills, borrow, funding, taxes or capacity model",
            "No corporate-action adjustment or delisting treatment; input returns must already be point-in-time adjusted",
            "Metrics are descriptive and are not evidence of future performance",
        ],
    }
