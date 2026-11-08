"""Authorized feed adapter interface with entitlement binding (#178).

Adapters are the only sanctioned path for external market data into research.
Every adapter must be bound to a code-reviewed registry entry whose record
satisfies the operator feed-admission policy; unregistered adapters are
refused before any tick is read. The sandbox ships a deterministic synthetic
adapter only. No real vendor integration, redistribution right, or data
license is claimed by this module.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any, Iterable, Mapping, Protocol

from .feed_admission import FeedAdmissionRejection, admit_feed

FEED_TICK_SCHEMA = "osiris.feed_tick.v1"
DEFAULT_MAX_DELAY_SECONDS = 300.0
MAX_FUTURE_SKEW_SECONDS = 60.0
MAX_TICKS_PER_FETCH = 10_000
MAX_TEXT_LENGTH = 200


class FeedAdapterRejection(ValueError):
    """Raised when an adapter, its entitlement binding, or a tick is refused."""


class FeedAdapter(Protocol):
    """Interface every authorized feed adapter must implement."""

    name: str

    def fetch(self, *, now: datetime) -> list[dict[str, Any]]:
        """Return raw tick dicts; may be empty, never None."""


# Code-reviewed adapter registry. Keys are adapter names; values are policy
# records in the exact shape admit_feed() consumes, plus a license_class note.
# synthetic_test_data entries exist so the gate can be exercised end to end
# without any real vendor data.
ADAPTER_ENTITLEMENTS: dict[str, dict[str, Any]] = {
    "synthetic-authorized": {
        "authorized_source": "synthetic-deterministic-fixture",
        "entitlement_reference": "synthetic-fixture-no-vendor-data",
        "redistribution_rights": "permitted",
        "availability_timestamps_required": True,
        "license_class": "synthetic_test_data",
        "quality_review": {
            "status": "passed",
            "reviewer": "workstream-178-code-review",
            "reviewed_at": "2026-11-08T00:00:00Z",
        },
    },
}


def _utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise FeedAdapterRejection(f"tick_{field}_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FeedAdapterRejection(f"tick_{field}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FeedAdapterRejection(f"tick_{field}_missing_timezone")
    return parsed.astimezone(timezone.utc)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > MAX_TEXT_LENGTH:
        raise FeedAdapterRejection(f"tick_{field}_required")
    return value.strip()


def validate_feed_tick(
    tick: Any,
    *,
    now: datetime,
    max_delay_seconds: float = DEFAULT_MAX_DELAY_SECONDS,
) -> dict[str, Any]:
    """Validate one point-in-time feed tick; return the normalized tick.

    Ordering rule: event_time <= available_time <= received_time. A tick whose
    available_time is in the future, or whose received->available delay exceeds
    max_delay_seconds, is rejected so stale or not-yet-available data can never
    enter research as valid.
    """
    if not isinstance(tick, Mapping):
        raise FeedAdapterRejection("tick_not_an_object")
    if tick.get("schema") != FEED_TICK_SCHEMA:
        raise FeedAdapterRejection("tick_schema_unsupported")
    if isinstance(max_delay_seconds, bool) or not isinstance(max_delay_seconds, (int, float)) \
            or not 0 <= max_delay_seconds <= 86_400:
        raise FeedAdapterRejection("tick_max_delay_bound_invalid")
    symbol = _text(tick.get("symbol"), "symbol")
    source = _text(tick.get("source"), "source")
    event_time = _utc(tick.get("event_time"), "event_time")
    available_time = _utc(tick.get("available_time"), "available_time")
    received_time = _utc(tick.get("received_time"), "received_time")
    if not event_time <= available_time <= received_time:
        raise FeedAdapterRejection("tick_time_order_violated")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise FeedAdapterRejection("tick_now_missing_timezone")
    current = now.astimezone(timezone.utc)
    if (available_time - current).total_seconds() > MAX_FUTURE_SKEW_SECONDS:
        raise FeedAdapterRejection("tick_available_in_future")
    if (received_time - available_time).total_seconds() > max_delay_seconds:
        raise FeedAdapterRejection("tick_stale_beyond_delay")
    try:
        price = Decimal(str(tick.get("price")))
    except (InvalidOperation, TypeError) as exc:
        raise FeedAdapterRejection("tick_price_invalid") from exc
    if not price.is_finite() or price <= 0:
        raise FeedAdapterRejection("tick_price_not_positive_finite")
    return {
        "schema": FEED_TICK_SCHEMA,
        "symbol": symbol,
        "source": source,
        "event_time": event_time.isoformat(),
        "available_time": available_time.isoformat(),
        "received_time": received_time.isoformat(),
        "price": str(price),
    }


def _adapter_name(adapter: Any) -> str:
    name = getattr(adapter, "name", None)
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > MAX_TEXT_LENGTH:
        raise FeedAdapterRejection("adapter_name_invalid")
    return name.strip()


def ingest_adapter_ticks(
    adapter: FeedAdapter,
    *,
    instruments: Iterable[str],
    policy: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    max_delay_seconds: float = DEFAULT_MAX_DELAY_SECONDS,
) -> dict[str, Any]:
    """Fetch and gate adapter ticks. Unregistered adapters are refused BEFORE
    fetch() is called; individual bad ticks are rejected with reasons while
    valid ticks are admitted. Never raises for per-tick data problems."""
    name = _adapter_name(adapter)
    registry = ADAPTER_ENTITLEMENTS.get(name)
    if registry is None:
        raise FeedAdapterRejection("adapter_not_registered")
    instrument_list = [str(i).strip() for i in instruments if str(i).strip()]
    if not instrument_list or len(instrument_list) > 1_000:
        raise FeedAdapterRejection("instruments_required_bounded")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise FeedAdapterRejection("ingest_time_missing_timezone")
    current = current.astimezone(timezone.utc)

    merged_policy: dict[str, Any] = {name: {**registry, "instruments": set(instrument_list)}}
    if policy:
        for key, record in policy.items():
            merged_policy[str(key)] = record
    admissions: dict[str, Any] = {}
    for instrument in instrument_list:
        try:
            admissions[instrument] = admit_feed(
                provider=name, instrument=instrument, policy=merged_policy, now=current,
            )
        except FeedAdmissionRejection as exc:
            raise FeedAdapterRejection("adapter_entitlement_rejected:" + str(exc)) from exc

    raw = adapter.fetch(now=current)
    if not isinstance(raw, list) or len(raw) > MAX_TICKS_PER_FETCH:
        raise FeedAdapterRejection("adapter_fetch_result_invalid")

    admitted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, tick in enumerate(raw):
        try:
            normalized = validate_feed_tick(
                tick, now=current, max_delay_seconds=max_delay_seconds,
            )
        except FeedAdapterRejection as exc:
            rejected.append({"index": index, "reason": str(exc)})
            continue
        if normalized["symbol"] not in instrument_list:
            rejected.append({"index": index, "reason": "tick_instrument_not_authorized"})
            continue
        admitted.append(normalized)
    return {
        "status": "admitted" if admitted else "no_ticks_admitted",
        "adapter": name,
        "license_class": registry.get("license_class"),
        "admissions": admissions,
        "admitted": admitted,
        "rejected": rejected,
        "counts": {"fetched": len(raw), "admitted": len(admitted), "rejected": len(rejected)},
        "verification_boundary": "operator_attestation_and_synthetic_fixtures_not_vendor_verification",
    }


class SyntheticAuthorizedAdapter:
    """Deterministic synthetic feed for sandbox research-gate evidence.

    NOT a vendor adapter. Ticks are a seeded hash-random walk; `inject`
    deliberately emits delayed, malformed, stale, future-available and
    bad-price ticks so the admission gate's failure modes are exercised.
    """

    name = "synthetic-authorized"

    def __init__(
        self,
        *,
        seed: str,
        symbols: list[str],
        start: datetime,
        step_seconds: int = 60,
        count: int = 16,
        base_price: str = "100.00",
        inject: Iterable[str] = (),
    ) -> None:
        if not seed or len(seed) > 128:
            raise FeedAdapterRejection("synthetic_seed_invalid")
        if not symbols or len(symbols) > 100:
            raise FeedAdapterRejection("synthetic_symbols_invalid")
        if start.tzinfo is None or start.utcoffset() is None:
            raise FeedAdapterRejection("synthetic_start_missing_timezone")
        if not 1 <= step_seconds <= 86_400 or not 1 <= count <= MAX_TICKS_PER_FETCH:
            raise FeedAdapterRejection("synthetic_schedule_invalid")
        Decimal(base_price)  # raises InvalidOperation on malformed input
        self._seed = seed
        self._symbols = list(symbols)
        self._start = start.astimezone(timezone.utc)
        self._step = step_seconds
        self._count = count
        self._base = Decimal(base_price)
        self._inject = set(inject)

    def _walk_price(self, symbol: str, index: int) -> Decimal:
        price = self._base
        for step in range(index + 1):
            digest = hashlib.sha256(f"{self._seed}|{symbol}|{step}".encode()).digest()
            direction = 1 if digest[0] % 2 == 0 else -1
            magnitude = Decimal(digest[1] % 21) / Decimal(100)  # 0.00..0.20
            price = max(Decimal("0.01"), price + direction * magnitude)
        return price.quantize(Decimal("0.0001"))

    def fetch(self, *, now: datetime) -> list[dict[str, Any]]:
        ticks: list[dict[str, Any]] = []
        for index in range(self._count):
            symbol = self._symbols[index % len(self._symbols)]
            event = self._start + timedelta(seconds=self._step * index)
            tick: dict[str, Any] = {
                "schema": FEED_TICK_SCHEMA,
                "symbol": symbol,
                "source": self.name,
                "event_time": event.isoformat(),
                "available_time": (event + timedelta(seconds=1)).isoformat(),
                "received_time": (event + timedelta(seconds=2)).isoformat(),
                "price": str(self._walk_price(symbol, index)),
            }
            if "delayed" in self._inject and index == 1:
                tick["available_time"] = (event + timedelta(seconds=1)).isoformat()
                tick["received_time"] = (
                    event + timedelta(seconds=1 + DEFAULT_MAX_DELAY_SECONDS + 60)
                ).isoformat()
            if "malformed" in self._inject and index == 2:
                tick = {"schema": FEED_TICK_SCHEMA, "symbol": symbol}
            if "stale" in self._inject and index == 3:
                tick["received_time"] = (
                    event + timedelta(seconds=1 + 86_400)
                ).isoformat()
            if "future_available" in self._inject and index == 4:
                tick["available_time"] = (now + timedelta(hours=1)).isoformat()
                tick["received_time"] = (now + timedelta(hours=1, seconds=1)).isoformat()
            if "bad_price" in self._inject and index == 5:
                tick["price"] = "-1"
            ticks.append(tick)
        return ticks


def ticks_digest(ticks: list[Mapping[str, Any]]) -> str:
    """Deterministic content hash over normalized ticks (order-sensitive)."""
    canonical = json.dumps(list(ticks), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
