"""Fail-closed simulation risk checks; no broker integration."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class RiskLimits:
    max_order_notional: float = 10000.0
    max_position_notional: float = 50000.0
    max_daily_loss: float = 1000.0


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


def check_order(
    *,
    quantity: float,
    price: float,
    current_position_notional: float,
    daily_pnl: float,
    limits: RiskLimits = RiskLimits(),
) -> tuple[bool, str]:
    """Preserve the API reason strings while matching Rust decision semantics."""
    values = (quantity, price, current_position_notional, daily_pnl)
    if any(not _is_finite_number(value) for value in values):
        return False, "invalid_numeric_input"

    if (
        quantity <= 0
        or price <= 0
        or current_position_notional < 0
    ):
        return False, "invalid_order_or_position"

    limit_values = (
        limits.max_order_notional,
        limits.max_position_notional,
        limits.max_daily_loss,
    )
    if any(not _is_finite_number(value) for value in limit_values):
        return False, "invalid_risk_limits"
    if (
        limits.max_order_notional <= 0
        or limits.max_position_notional <= 0
        or limits.max_daily_loss < 0
    ):
        return False, "invalid_risk_limits"

    try:
        notional = float(quantity) * float(price)
        projected_position = float(current_position_notional) + notional
    except (OverflowError, ValueError):
        return False, "invalid_numeric_input"

    if not math.isfinite(notional) or not math.isfinite(projected_position):
        return False, "invalid_numeric_input"
    if notional > limits.max_order_notional:
        return False, "max_order_notional_exceeded"
    if projected_position > limits.max_position_notional:
        return False, "max_position_notional_exceeded"
    if daily_pnl <= -limits.max_daily_loss:
        return False, "daily_loss_limit_reached"
    return True, "approved_for_simulation_only"
