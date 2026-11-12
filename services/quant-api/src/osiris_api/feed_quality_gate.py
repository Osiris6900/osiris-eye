"""Fail-closed, deterministic validation of authorized market observations.

This is an offline quality gate, not a live feed adapter or entitlement service.
"""
import math
from dataclasses import dataclass
from datetime import datetime


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
class FeedDecision:
    accepted: bool
    reason: str


def _finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def validate_observation(
    observation: FeedObservation,
    *,
    now: datetime,
    previous_event_time: datetime | None = None,
    max_age_seconds: float = 5.0,
) -> FeedDecision:
    """Require explicit trust and fresh event time, even after delayed delivery."""
    def reject(reason: str) -> FeedDecision:
        return FeedDecision(False, reason)

    if not isinstance(observation, FeedObservation):
        return reject("invalid_observation")
    timestamps = (observation.event_time, observation.available_time, now)
    if any(not isinstance(t, datetime) or t.tzinfo is None or
           t.utcoffset() is None for t in timestamps):
        return reject("timezone_required")
    if previous_event_time is not None and (
        not isinstance(previous_event_time, datetime) or
        previous_event_time.tzinfo is None or
        previous_event_time.utcoffset() is None
    ):
        return reject("timezone_required")
    if observation.entitled is not True:
        return reject("not_entitled")
    if observation.authenticated is not True:
        return reject("unauthenticated")
    if any(not isinstance(value, str) or not value.strip()
           for value in (observation.source, observation.instrument)):
        return reject("invalid_identity")
    if not _finite_number(observation.price) or observation.price <= 0:
        return reject("invalid_price")
    if observation.event_time > now or observation.available_time > now:
        return reject("future_timestamp")
    if observation.available_time < observation.event_time:
        return reject("invalid_timestamp_order")
    if previous_event_time is not None and observation.event_time <= previous_event_time:
        return reject("out_of_order")
    if not _finite_number(max_age_seconds) or max_age_seconds <= 0:
        return reject("invalid_max_age")
    # A recent retrieval cannot make an old price safe for an execution decision.
    # available_time >= event_time above also bounds the availability age.
    if (now - observation.event_time).total_seconds() > max_age_seconds:
        return reject("stale")
    return FeedDecision(True, "accepted")
