"""Safe paper-trading workflow joining research, backtest, risk and audit layers.

This module creates paper order intents only. It never calls a broker or exchange.
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Sequence

from .backtesting import simulate_signals
from .risk import RiskLimits, check_order


def evaluate_paper_workflow(
    *,
    symbol: str,
    signals: Sequence[float],
    asset_returns: Sequence[float],
    quantity: float,
    price: float,
    current_position_notional: float = 0.0,
    daily_pnl: float = 0.0,
    cost_bps_per_unit_turnover: float = 2.0,
    limits: RiskLimits = RiskLimits(),
) -> dict[str, Any]:
    """Backtest a candidate signal stream and risk-gate one paper order intent."""
    if not isinstance(symbol, str):
        raise ValueError("invalid_symbol")
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol or len(normalized_symbol) > 64:
        raise ValueError("invalid_symbol")
    numeric = (quantity, price, current_position_notional, daily_pnl,
               cost_bps_per_unit_turnover)
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not isfinite(float(value)) for value in numeric):
        raise ValueError("invalid_numeric_input")

    backtest = simulate_signals(
        signals, asset_returns,
        cost_bps_per_unit_turnover=cost_bps_per_unit_turnover,
    )
    approved, reason = check_order(
        quantity=float(quantity),
        price=float(price),
        current_position_notional=float(current_position_notional),
        daily_pnl=float(daily_pnl),
        limits=limits,
    )
    return {
        "workflow": "research_to_paper_risk_gate",
        "symbol": normalized_symbol,
        "backtest": backtest,
        "risk": {
            "approved_for_paper": approved,
            "reason": reason,
            "order_notional": float(quantity) * float(price),
        },
        "paper_order_intent": {
            "symbol": normalized_symbol,
            "side": "BUY" if float(signals[-1]) > 0 else
                    "SELL" if float(signals[-1]) < 0 else "HOLD",
            "quantity": float(quantity),
            "reference_price": float(price),
            "status": "PAPER_INTENT_APPROVED" if approved else "BLOCKED_BY_RISK",
        },
        "execution": {
            "mode": "paper_only",
            "broker_order_submitted": False,
            "live_execution": False,
        },
    }
