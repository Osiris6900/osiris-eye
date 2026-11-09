"""Fail-closed, vendor-neutral streaming tick validation for research and paper trading.

No network connection or live order routing is performed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping


class FeedRejected(ValueError):
    """Tick cannot be used for research or paper-order authorization."""


@dataclass(frozen=True)
class ValidatedTick:
    provider: str
    instrument_id: str
    price: Decimal
    event_time: datetime
    available_at: datetime
    sequence: int


class StreamingFeedGate:
    """Enforce entitlement, freshness, ordering and point-in-time availability.

    State changes only after every validation succeeds. Instances are single-threaded;
    callers should serialize concurrent ingest or use separate instances per stream.
    """

    def __init__(
        self,
        entitlements: Mapping[str, set[str]],
        *,
        max_age: timedelta = timedelta(seconds=10),
        max_future_skew: timedelta = timedelta(seconds=2),
    ) -> None:
        if max_age <= timedelta(0) or max_future_skew < timedelta(0):
            raise ValueError("invalid feed timing limits")
        self._entitlements = {k: frozenset(v) for k, v in entitlements.items()}
        self._max_age = max_age
        self._max_future_skew = max_future_skew
        self._last: dict[tuple[str, str], tuple[int, datetime]] = {}

    @staticmethod
    def _utc(value: datetime, name: str) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise FeedRejected(f"{name} must be timezone-aware")
        return value.astimezone(timezone.utc)

    def ingest(
        self,
        *,
        provider: str,
        instrument_id: str,
        price: str,
        event_time: datetime,
        available_at: datetime,
        sequence: int,
        now: datetime,
    ) -> ValidatedTick:
        if not provider or not instrument_id:
            raise FeedRejected("provider and instrument_id are required")
        if instrument_id not in self._entitlements.get(provider, frozenset()):
            raise FeedRejected("feed not entitled")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise FeedRejected("invalid sequence")
        try:
            parsed_price = Decimal(price)
        except (InvalidOperation, ValueError, TypeError):
            raise FeedRejected("invalid price") from None
        if not parsed_price.is_finite() or parsed_price <= 0:
            raise FeedRejected("price must be finite and positive")

        event = self._utc(event_time, "event_time")
        available = self._utc(available_at, "available_at")
        clock = self._utc(now, "now")
        if event > available:
            raise FeedRejected("observation available before event")
        if available > clock + self._max_future_skew or event > clock + self._max_future_skew:
            raise FeedRejected("future observation")
        if clock - event > self._max_age or clock - available > self._max_age:
            raise FeedRejected("stale observation")

        key = (provider, instrument_id)
        previous = self._last.get(key)
        if previous is not None and (sequence <= previous[0] or event < previous[1]):
            raise FeedRejected("duplicate or out-of-order observation")

        tick = ValidatedTick(provider, instrument_id, parsed_price, event, available, sequence)
        self._last[key] = (sequence, event)
        return tick

    def latest_sequence(self, provider: str, instrument_id: str) -> int | None:
        previous = self._last.get((provider, instrument_id))
        return None if previous is None else previous[0]
