"""Fail-closed paper-order authorization across feed, model and risk boundaries.

This module does not connect to brokers or place orders.
"""
from dataclasses import dataclass

from .risk import RiskLimits, check_execution


@dataclass(frozen=True)
class PaperOrderContext:
    quantity: float
    price: float
    current_position_notional: float
    daily_pnl: float
    observation_time_ms: int
    decision_time_ms: int
    model_approved: bool
    feed_authenticated: bool
    feed_entitled: bool
    kill_switch_active: bool
    max_feed_age_ms: int = 5000
    current_gross_exposure: float | None = None


def authorize_paper_order(
    context: PaperOrderContext, limits: RiskLimits = RiskLimits()
) -> tuple[bool, str]:
    """Authorize simulation only after all independent gates pass."""
    if context.kill_switch_active is not False:
        return False, "kill_switch_active"
    if context.feed_authenticated is not True:
        return False, "feed_unauthenticated"
    if context.feed_entitled is not True:
        return False, "feed_unentitled"
    if context.model_approved is not True:
        return False, "model_not_approved"
    if (
        isinstance(context.observation_time_ms, bool)
        or isinstance(context.decision_time_ms, bool)
        or isinstance(context.max_feed_age_ms, bool)
        or not all(
            isinstance(v, int)
            for v in (
                context.observation_time_ms,
                context.decision_time_ms,
                context.max_feed_age_ms,
            )
        )
        or context.observation_time_ms < 0
        or context.decision_time_ms < 0
        or context.max_feed_age_ms < 0
    ):
        return False, "invalid_feed_timestamp"
    age = context.decision_time_ms - context.observation_time_ms
    if age < 0:
        return False, "future_observation"
    if age > context.max_feed_age_ms:
        return False, "stale_feed"
    if context.current_gross_exposure is None:
        return False, "exposure_input_missing"
    # This gate has no side. Project the order as a buy, which adds gross.
    approved, reason, _evaluated = check_execution(
        side="BUY",
        quantity=context.quantity,
        price=context.price,
        current_position_notional=context.current_position_notional,
        current_gross_exposure=context.current_gross_exposure,
        daily_pnl=context.daily_pnl,
        limits=limits,
        kill_switch_active=False,
        market_data_fresh=True,
        risk_service_available=True,
    )
    return approved, reason
