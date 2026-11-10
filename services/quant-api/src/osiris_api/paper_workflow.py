"""Safe paper-trading workflow joining research, backtest, risk and audit layers.

This module creates paper order intents only. It never calls a broker or exchange.
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Sequence

from .backtesting import simulate_signals
from .risk import RiskLimits, check_execution, pretrade_gate


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
    enforce_execution_controls: bool = True,
    kill_switch_active: bool = True,
    market_data_fresh: bool = False,
    risk_service_available: bool = False,
    current_gross_exposure: float | None = None,
) -> dict[str, Any]:
    """Backtest signals; only clear all pretrade gates to approve a paper intent.

    Missing or explicitly disabled execution controls fail closed. No live order
    is submitted and this helper must not be treated as a paper fill engine.
    """
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
    if enforce_execution_controls is True:
        clear, gate = pretrade_gate(
            kill_switch_active=kill_switch_active,
            market_data_fresh=market_data_fresh,
            risk_service_available=risk_service_available,
        )
        last_signal = float(signals[-1])
        derived_side = "BUY" if last_signal > 0 else "SELL" if last_signal < 0 else "HOLD"
        if not clear:
            approved, reason, limits_evaluated = False, gate, False
        elif derived_side == "HOLD":
            approved, reason, limits_evaluated = False, "hold_signal_no_order", False
        elif current_gross_exposure is None:
            approved, reason, limits_evaluated = False, "exposure_input_missing", False
        else:
            approved, reason, limits_evaluated = check_execution(
                side=derived_side,
                quantity=float(quantity),
                price=float(price),
                current_position_notional=float(current_position_notional),
                current_gross_exposure=current_gross_exposure,
                daily_pnl=float(daily_pnl),
                limits=limits,
                kill_switch_active=kill_switch_active,
                market_data_fresh=market_data_fresh,
                risk_service_available=risk_service_available,
            )
    else:
        # A former permissive research-only branch could approve a paper order
        # despite stale feeds, kill-switch, or missing risk-service evidence.
        # Preserve caller compatibility, but never issue an approved intent.
        approved, reason, limits_evaluated = False, "execution_controls_required", False
    risk = {
        "approved_for_paper": approved,
        "reason": reason,
        "order_notional": float(quantity) * float(price),
    }
    risk["limits_evaluated"] = limits_evaluated
    risk["live_execution"] = False
    return {
        "workflow": "research_to_paper_risk_gate",
        "symbol": normalized_symbol,
        "backtest": backtest,
        "risk": risk,
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
