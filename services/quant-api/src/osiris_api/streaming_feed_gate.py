"""Fail-closed, vendor-neutral streaming tick validation for research and paper trading.

No network connection or live order routing is performed here. A gap, a stale
tick, or a heartbeat cannot make a price look fresh, and nothing in this
module enables a live order.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


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


@dataclass(frozen=True)
class FeedStatus:
    """Point-in-time view of one instrument stream.

    ``price`` is present only when ``state`` is ``fresh``. A gapped or stale
    stream withholds the last price so a caller cannot treat it as current.
    ``live_orders_enabled`` is always false.
    """

    state: str
    usable: bool
    sequence: int | None
    price: Decimal | None
    event_time: datetime | None
    transport_alive: bool
    live_orders_enabled: bool = False

    def __post_init__(self) -> None:
        if self.live_orders_enabled:
            raise RuntimeError("live_orders_forbidden")
        if self.state not in {"unknown", "fresh", "stale", "gapped"}:
            raise RuntimeError("invalid_feed_state")
        if self.usable != (self.state == "fresh"):
            raise RuntimeError("freshness_usable_mismatch")
        if self.state == "fresh" and self.price is None:
            raise RuntimeError("fresh_price_required")
        if self.state != "fresh" and self.price is not None:
            raise RuntimeError("non_fresh_price_leaked")


def authorize_feed_use(status: FeedStatus) -> dict[str, Any]:
    """Allow a fresh price for research only. Never authorizes a live order."""
    if not isinstance(status, FeedStatus):
        raise TypeError("feed_status_required")
    fresh = status.state == "fresh" and status.usable and status.price is not None
    return {
        "accepted": fresh,
        "reason": "fresh_for_research_only" if fresh else f"feed_{status.state}",
        "price": str(status.price) if fresh else None,
        "sequence": status.sequence if fresh else None,
        "live_orders_enabled": False,
        "broker_order_submitted": False,
    }


class StreamingFeedGate:
    """Enforce entitlement, freshness, ordering, gaps and point-in-time availability.

    The accepted cursor moves only after every check succeeds. A sequence hole
    latches a gap and does not move the cursor; incremental ticks stay rejected
    until :meth:`resync` applies a newer snapshot. Heartbeats update transport
    liveness only — they never refresh a price. Instances are single-threaded;
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
        self._last: dict[tuple[str, str], tuple[int, datetime, datetime, Decimal]] = {}
        self._gapped: set[tuple[str, str]] = set()
        self._transport_at: dict[tuple[str, str], datetime] = {}

    @staticmethod
    def _utc(value: datetime, name: str) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise FeedRejected(f"{name} must be timezone-aware")
        return value.astimezone(timezone.utc)

    def _prepare(
        self,
        *,
        provider: str,
        instrument_id: str,
        price: str,
        event_time: datetime,
        available_at: datetime,
        sequence: int,
        now: datetime,
    ) -> tuple[str, str, Decimal, datetime, datetime, datetime, int]:
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
        return provider, instrument_id, parsed_price, event, available, clock, sequence

    def _touch(self, key: tuple[str, str], clock: datetime) -> None:
        current = self._transport_at.get(key)
        if current is None or clock > current:
            self._transport_at[key] = clock

    def _store(
        self,
        key: tuple[str, str],
        provider: str,
        instrument_id: str,
        parsed_price: Decimal,
        event: datetime,
        available: datetime,
        sequence: int,
        clock: datetime,
    ) -> ValidatedTick:
        tick = ValidatedTick(provider, instrument_id, parsed_price, event, available, sequence)
        self._last[key] = (sequence, event, available, parsed_price)
        self._gapped.discard(key)
        self._touch(key, clock)
        return tick

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
        provider, instrument_id, parsed_price, event, available, clock, sequence = self._prepare(
            provider=provider,
            instrument_id=instrument_id,
            price=price,
            event_time=event_time,
            available_at=available_at,
            sequence=sequence,
            now=now,
        )
        key = (provider, instrument_id)
        if key in self._gapped:
            self._touch(key, clock)
            raise FeedRejected("sequence gap; resync required")
        previous = self._last.get(key)
        if previous is not None and (sequence <= previous[0] or event < previous[1]):
            raise FeedRejected("duplicate or out-of-order observation")
        if previous is not None and sequence > previous[0] + 1:
            self._gapped.add(key)
            self._touch(key, clock)
            raise FeedRejected("sequence gap")
        return self._store(
            key, provider, instrument_id, parsed_price, event, available, sequence, clock,
        )

    def resync(
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
        """Replace the cursor with a snapshot. This is the only way to clear a gap.

        A gap stays latched unless the snapshot sequence is strictly newer than
        the last accepted sequence. Restating the pre-gap tick must not look
        like a continuous fresh book.
        """
        provider, instrument_id, parsed_price, event, available, clock, sequence = self._prepare(
            provider=provider,
            instrument_id=instrument_id,
            price=price,
            event_time=event_time,
            available_at=available_at,
            sequence=sequence,
            now=now,
        )
        key = (provider, instrument_id)
        previous = self._last.get(key)
        if previous is not None:
            if sequence < previous[0] or event < previous[1]:
                raise FeedRejected("snapshot behind cursor")
            if key in self._gapped and sequence <= previous[0]:
                raise FeedRejected("snapshot does not advance cursor")
            if sequence == previous[0] and event == previous[1] and parsed_price != previous[3]:
                raise FeedRejected("snapshot conflicts with cursor")
        return self._store(
            key, provider, instrument_id, parsed_price, event, available, sequence, clock,
        )

    def note_heartbeat(self, *, provider: str, instrument_id: str, now: datetime) -> None:
        """Record that the transport spoke. Does not refresh price or sequence."""
        if not provider or not instrument_id:
            raise FeedRejected("provider and instrument_id are required")
        if instrument_id not in self._entitlements.get(provider, frozenset()):
            raise FeedRejected("feed not entitled")
        self._touch((provider, instrument_id), self._utc(now, "now"))

    def latest_sequence(self, provider: str, instrument_id: str) -> int | None:
        previous = self._last.get((provider, instrument_id))
        return None if previous is None else previous[0]

    def gap_open(self, provider: str, instrument_id: str) -> bool:
        return (provider, instrument_id) in self._gapped

    def status(self, provider: str, instrument_id: str, now: datetime) -> FeedStatus:
        if not provider or not instrument_id:
            raise FeedRejected("provider and instrument_id are required")
        clock = self._utc(now, "now")
        key = (provider, instrument_id)
        transport_at = self._transport_at.get(key)
        alive = (
            transport_at is not None
            and timedelta(0) <= clock - transport_at <= self._max_age
        )
        previous = self._last.get(key)
        if previous is None:
            state = "gapped" if key in self._gapped else "unknown"
            return FeedStatus(state, False, None, None, None, alive)
        sequence, event, available, price = previous
        too_old = clock - event > self._max_age or clock - available > self._max_age
        not_yet = available > clock or event > clock + self._max_future_skew
        if key in self._gapped:
            state = "gapped"
        elif too_old or not_yet:
            state = "stale"
        else:
            state = "fresh"
        return FeedStatus(
            state=state,
            usable=state == "fresh",
            sequence=sequence,
            price=price if state == "fresh" else None,
            event_time=event,
            transport_alive=alive,
        )
