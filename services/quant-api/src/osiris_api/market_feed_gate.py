"""Point-in-time market observation validation; no vendor or exchange connection."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class MarketObservation:
    symbol: str
    price: float
    event_time_ms: int
    received_time_ms: int
    authenticated: bool
    entitled: bool
    source: str


def validate_market_observation(
    observation: MarketObservation,
    *,
    decision_time_ms: int,
    max_age_ms: int = 5000,
    previous_event_time_ms: int | None = None,
) -> tuple[bool, str]:
    """Fail closed on invalid provenance, time, value and entitlement."""
    if not observation.authenticated:
        return False, "source_unauthenticated"
    if not observation.entitled:
        return False, "source_unentitled"
    if not observation.symbol.strip() or not observation.source.strip():
        return False, "missing_instrument_or_source"
    if isinstance(observation.price, bool) or not isinstance(observation.price, (int, float)):
        return False, "invalid_price"
    try:
        if not math.isfinite(float(observation.price)) or observation.price <= 0:
            return False, "invalid_price"
    except (OverflowError, ValueError):
        return False, "invalid_price"
    timestamps = (
        observation.event_time_ms,
        observation.received_time_ms,
        decision_time_ms,
        max_age_ms,
    )
    if any(type(value) is not int or value < 0 for value in timestamps):
        return False, "invalid_timestamp"
    if previous_event_time_ms is not None and (
        type(previous_event_time_ms) is not int or previous_event_time_ms < 0
    ):
        return False, "invalid_previous_timestamp"
    if observation.event_time_ms > observation.received_time_ms:
        return False, "event_after_receipt"
    if observation.received_time_ms > decision_time_ms:
        return False, "future_receipt"
    if previous_event_time_ms is not None and observation.event_time_ms <= previous_event_time_ms:
        return False, "out_of_order_observation"
    if decision_time_ms - observation.event_time_ms > max_age_ms:
        return False, "stale_observation"
    return True, "accepted_for_research_only"
