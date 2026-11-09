"""Fail-closed, deterministic validation of authorized market observations.

This is an offline quality gate, not a live feed adapter or entitlement service.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import math


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


def validate_observation(
    observation: FeedObservation,
    *,
    now: datetime,
    previous_event_time: datetime | None = None,
    max_age_seconds: float = 5.0,
) -> FeedDecision:
    def reject(reason: str) -> FeedDecision:
        return FeedDecision(False, reason)

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
    if not observation.entitled:
        return reject("not_entitled")
    if not observation.authenticated:
        return reject("unauthenticated")
    if not observation.source.strip() or not observation.instrument.strip():
        return reject("invalid_identity")
    if isinstance(observation.price, bool) or not isinstance(observation.price, (float, int)) or (
        not math.isfinite(observation.price) or observation.price <= 0
    ):
        return reject("invalid_price")
    if observation.event_time > now or observation.available_time > now:
        return reject("future_timestamp")
    if observation.available_time < observation.event_time:
        return reject("invalid_timestamp_order")
    if previous_event_time is not None and observation.event_time <= previous_event_time:
        return reject("out_of_order")
    if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
        return reject("invalid_max_age")
    if (now - observation.available_time).total_seconds() > max_age_seconds:
        return reject("stale")
    return FeedDecision(True, "accepted")
