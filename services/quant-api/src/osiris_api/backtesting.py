"""Small, transparent signal/return simulator; not an execution simulator."""
from __future__ import annotations

from math import isfinite
from typing import Sequence


def simulate_signals(
    signals: Sequence[float],
    asset_returns: Sequence[float],
    *,
    cost_bps_per_unit_turnover: float = 0.0,
) -> dict[str, object]:
    """Apply signal[t-1] to return[t] to prevent same-period look-ahead.

    Signals are target exposures in [-1, 1]. Turnover is absolute exposure change.
    This deliberately excludes fills, market impact, funding, borrow and capacity.
    """
    if len(signals) != len(asset_returns) or len(signals) < 2:
        raise ValueError("signals and asset_returns must have equal length >= 2")
    values = [float(x) for x in signals]
    returns = [float(x) for x in asset_returns]
    if not all(isfinite(x) and -1.0 <= x <= 1.0 for x in values):
        raise ValueError("signals must be finite exposures between -1 and 1")
    if not all(isfinite(x) and x > -1.0 for x in returns):
        raise ValueError("asset returns must be finite and greater than -100%")
    if not isfinite(cost_bps_per_unit_turnover) or cost_bps_per_unit_turnover < 0:
        raise ValueError("cost_bps_per_unit_turnover must be finite and non-negative")
    strategy_returns: list[float] = []
    turnovers: list[float] = []
    previous_exposure = 0.0
    for index in range(1, len(values)):
        exposure = values[index - 1]
        turnover = abs(exposure - previous_exposure)
        cost = turnover * cost_bps_per_unit_turnover / 10000.0
        strategy_returns.append(exposure * returns[index] - cost)
        turnovers.append(turnover)
        previous_exposure = exposure
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in strategy_returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak)
    return {
        "periods": len(strategy_returns),
        "strategy_returns": strategy_returns,
        "total_return": equity - 1.0,
        "max_drawdown": max_drawdown,
        "total_turnover": sum(turnovers),
        "cost_bps_per_unit_turnover": cost_bps_per_unit_turnover,
        "signal_alignment": "signal[t-1]_applied_to_return[t]",
        "execution": "not_simulated",
    }
