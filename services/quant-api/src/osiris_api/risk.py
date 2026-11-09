"""Fail-closed simulation risk checks; no broker integration."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class RiskLimits:
    max_order_notional: float = 10000.0
    max_position_notional: float = 50000.0
    max_daily_loss: float = 1000.0
    max_gross_exposure: float = 100000.0


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


def pretrade_gate(
    *,
    kill_switch_active: object = True,
    market_data_fresh: object = False,
    risk_service_available: object = False,
) -> tuple[bool, str]:
    """Fail closed unless risk is reachable, the kill switch is off, and the feed is fresh.

    Defaults are closed. Approval is not possible from this gate, and it does not
    route orders.
    """
    if risk_service_available is not True:
        return False, "risk_service_unavailable"
    if kill_switch_active is not False:
        return False, "kill_switch_active"
    if market_data_fresh is not True:
        return False, "stale_market_data"
    return True, "pretrade_clear"


def check_execution(
    *,
    side: object,
    quantity: float,
    price: float,
    current_position_notional: float,
    current_gross_exposure: float,
    daily_pnl: float,
    limits: RiskLimits = RiskLimits(),
    kill_switch_active: object = True,
    market_data_fresh: object = False,
    risk_service_available: object = False,
) -> tuple[bool, str, bool]:
    """Paper-only execution gate. Never authorizes a live broker order.

    Returns ``(approved, reason, limits_evaluated)``. Kill-switch, stale-feed,
    and risk-service checks run before notional limits, so a tripped gate does
    not count as a risk approval.
    """
    clear, gate = pretrade_gate(
        kill_switch_active=kill_switch_active,
        market_data_fresh=market_data_fresh,
        risk_service_available=risk_service_available,
    )
    if not clear:
        return False, gate, False

    if any(
        not _is_finite_number(value)
        for value in (
            quantity, price, current_position_notional, current_gross_exposure, daily_pnl,
        )
    ):
        return False, "invalid_numeric_input", True
    limit_values = (
        limits.max_order_notional, limits.max_position_notional,
        limits.max_daily_loss, limits.max_gross_exposure,
    )
    if any(not _is_finite_number(value) for value in limit_values):
        return False, "invalid_risk_limits", True
    if (
        limits.max_order_notional <= 0
        or limits.max_position_notional <= 0
        or limits.max_daily_loss < 0
        or limits.max_gross_exposure <= 0
    ):
        return False, "invalid_risk_limits", True
    if (
        float(quantity) <= 0
        or float(price) <= 0
        or float(current_position_notional) < 0
        or float(current_gross_exposure) < 0
    ):
        return False, "invalid_order_or_position", True
    if not isinstance(side, str) or side.upper() not in ("BUY", "SELL"):
        return False, "invalid_order_or_position", True

    try:
        notional = float(quantity) * float(price)
    except (OverflowError, ValueError):
        return False, "invalid_numeric_input", True
    if not math.isfinite(notional):
        return False, "invalid_numeric_input", True
    if notional > limits.max_order_notional:
        return False, "max_order_notional_exceeded", True

    if side.upper() == "BUY":
        approved, reason = check_order(
            quantity=quantity,
            price=price,
            current_position_notional=current_position_notional,
            daily_pnl=daily_pnl,
            limits=limits,
        )
        if not approved:
            return False, reason, True
        projected_gross = float(current_gross_exposure) + notional
        if not math.isfinite(projected_gross):
            return False, "invalid_numeric_input", True
        if projected_gross > limits.max_gross_exposure:
            return False, "exposure_budget_exceeded", True
        return True, "approved_for_simulation_only", True

    if notional > float(current_position_notional) or notional > float(current_gross_exposure):
        return False, "invalid_order_or_position", True
    return True, "approved_for_simulation_only", True
