"""Fail-closed, dependency-free validation of incoming market observations.

This module does not connect to vendors or authorize orders.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Optional


@dataclass(frozen=True)
class FeedObservation:
    source: str
    instrument: str
    event_time: datetime
    available_time: datetime
    price: float
    entitled: bool
    authenticated: bool


@dataclass(frozen=True)
class GateResult:
    accepted: bool
    reason: str


def validate_observation(
    observation: FeedObservation,
    *,
    now: datetime,
    max_age_seconds: float = 5.0,
    previous_event_time: Optional[datetime] = None,
) -> GateResult:
    """Reject untrusted, stale, future, malformed and out-of-order observations."""
    if not observation.entitled:
        return GateResult(False, "not_entitled")
    if not observation.authenticated:
        return GateResult(False, "unauthenticated")
    if not observation.source.strip() or not observation.instrument.strip():
        return GateResult(False, "missing_identity")
    if not isfinite(observation.price) or observation.price <= 0:
        return GateResult(False, "invalid_price")
    if not isfinite(max_age_seconds) or max_age_seconds <= 0:
        return GateResult(False, "invalid_age_limit")
    timestamps = (now, observation.event_time, observation.available_time)
    if any(t.tzinfo is None or t.utcoffset() is None for t in timestamps):
        return GateResult(False, "timezone_required")
    if observation.available_time < observation.event_time:
        return GateResult(False, "invalid_availability")
    if observation.event_time > now or observation.available_time > now:
        return GateResult(False, "future_timestamp")
    age = (now - observation.available_time).total_seconds()
    if age > max_age_seconds:
        return GateResult(False, "stale")
    if previous_event_time is not None:
        if previous_event_time.tzinfo is None or previous_event_time.utcoffset() is None:
            return GateResult(False, "timezone_required")
        if observation.event_time <= previous_event_time:
            return GateResult(False, "out_of_order")
    return GateResult(True, "accepted")
