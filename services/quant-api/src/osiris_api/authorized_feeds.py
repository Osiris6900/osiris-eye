"""Authorized market data adapters for issue #178.

Only registry-bound adapters with a fresh operator attestation may be
fetched. Every adapter tick must satisfy the point-in-time contract
``osiris.feed_tick.v1``: event_time <= available_time <= received_time,
received within a bounded delay of ``now`` (default 300s, future skew max
60s). Unregistered or suspended adapters are refused BEFORE ``fetch()`` is
called; per-tick violations are rejected with explicit reason strings and
never silently coerced.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Protocol

from .feed_admission import FeedAdmissionRejection, admit_feed

FEED_TICK_SCHEMA = "osiris.feed_tick.v1"
DEFAULT_MAX_DELAY_SECONDS = 300.0
MAX_FUTURE_SKEW_SECONDS = 60.0
MAX_TICKS_PER_FETCH = 4096
MAX_TEXT_LENGTH = 128


class FeedAdapterRejection(ValueError):
    """Fail-closed refusal with a machine-readable reason string."""


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


# Runtime revocation set (issue #180). security_admin can suspend a registered
# adapter from the admin control centre; ingestion refuses suspended adapters
# BEFORE fetch. The set is process-local and resets on restart.
_SUSPENDED_ADAPTERS: set[str] = set()


def suspend_adapter(name: str) -> None:
    """Suspend a registered adapter; unknown names fail closed."""
    if name not in ADAPTER_ENTITLEMENTS:
        raise FeedAdapterRejection("adapter_not_registered")
    _SUSPENDED_ADAPTERS.add(name)


def restore_adapter(name: str) -> None:
    _SUSPENDED_ADAPTERS.discard(name)


def suspended_adapters() -> frozenset[str]:
    return frozenset(_SUSPENDED_ADAPTERS)


def is_adapter_suspended(name: str) -> bool:
    return name in _SUSPENDED_ADAPTERS


def _utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise FeedAdapterRejection(f"tick_{field}_required")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise FeedAdapterRejection(f"tick_{field}_invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FeedAdapterRejection(f"tick_{field}_missing_timezone")
    return parsed.astimezone(timezone.utc)


def _price(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise FeedAdapterRejection("tick_price_invalid")
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise FeedAdapterRejection("tick_price_invalid") from None
    if not price.is_finite() or price <= 0:
        raise FeedAdapterRejection("tick_price_not_positive_finite")
    return price


def validate_feed_tick(
    tick: Any,
    *,
    now: datetime,
    max_delay_seconds: float = DEFAULT_MAX_DELAY_SECONDS,
) -> dict[str, Any]:
    """Validate one tick against the point-in-time contract.

    Returns a normalized tick dict. Raises FeedAdapterRejection with an
    explicit reason for any violation."""
    if not isinstance(tick, dict):
        raise FeedAdapterRejection("tick_not_an_object")
    if tick.get("schema") != FEED_TICK_SCHEMA:
        raise FeedAdapterRejection("tick_schema_unsupported")
    if not isinstance(max_delay_seconds, (int, float)) or isinstance(
        max_delay_seconds, bool
    ) or not 0 < float(max_delay_seconds) <= 86_400:
        raise FeedAdapterRejection("tick_max_delay_bound_invalid")
    instrument = tick.get("instrument")
    if not isinstance(instrument, str) or not 1 <= len(instrument.strip()) <= MAX_TEXT_LENGTH:
        raise FeedAdapterRejection("tick_instrument_required")
    event_time = _utc(tick.get("event_time"), "event_time")
    available_time = _utc(tick.get("available_time"), "available_time")
    received_time = _utc(tick.get("received_time"), "received_time")
    if not event_time <= available_time <= received_time:
        raise FeedAdapterRejection("tick_time_order_violated")
    if now.tzinfo is None or now.utcoffset() is None:
        raise FeedAdapterRejection("tick_now_missing_timezone")
    current = now.astimezone(timezone.utc)
    if (available_time - current).total_seconds() > MAX_FUTURE_SKEW_SECONDS:
        raise FeedAdapterRejection("tick_available_in_future")
    if (current - received_time).total_seconds() > float(max_delay_seconds):
        raise FeedAdapterRejection("tick_stale_beyond_delay")
    price = _price(tick.get("price"))
    return {
        "schema": FEED_TICK_SCHEMA,
        "instrument": instrument.strip(),
        "event_time": event_time.isoformat(),
        "available_time": available_time.isoformat(),
        "received_time": received_time.isoformat(),
        "price": str(price),
    }


def _adapter_name(adapter: FeedAdapter) -> str:
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
    """Fetch and gate adapter ticks. Unregistered or suspended adapters are
    refused BEFORE fetch() is called; individual bad ticks are rejected with
    reasons while valid ticks are admitted. Never raises for per-tick data
    problems."""
    name = _adapter_name(adapter)
    registry = ADAPTER_ENTITLEMENTS.get(name)
    if registry is None:
        raise FeedAdapterRejection("adapter_not_registered")
    if name in _SUSPENDED_ADAPTERS:
        raise FeedAdapterRejection("adapter_suspended")
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
    rejected: list[dict[str, str]] = []
    for index, tick in enumerate(raw):
        try:
            normalized = validate_feed_tick(
                tick, now=current, max_delay_seconds=max_delay_seconds
            )
        except FeedAdapterRejection as exc:
            rejected.append({"index": str(index), "reason": str(exc)})
            continue
        if normalized["instrument"] not in instrument_list:
            rejected.append({"index": str(index), "reason": "tick_instrument_not_requested"})
            continue
        admitted.append(normalized)

    return {
        "adapter": name,
        "status": "admitted" if admitted else "no_ticks_admitted",
        "admissions": {
            instrument: {
                "provider": record["provider"],
                "instrument": record["instrument"],
                "admitted_at": record["admitted_at"],
            }
            for instrument, record in admissions.items()
        },
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
            digest = hashlib.sha256(f"{self._seed}:{symbol}:{step}".encode()).digest()
            delta = (int.from_bytes(digest[:2], "big") % 2001 - 1000) / 10000
            price = (price * (1 + Decimal(str(delta)))).quantize(Decimal("0.0001"))
        return price

    def fetch(self, *, now: datetime) -> list[dict[str, Any]]:
        ticks: list[dict[str, Any]] = []
        from datetime import timedelta

        for index in range(self._count):
            event = self._start + timedelta(seconds=self._step * index)
            available = event
            received = event
            instrument = self._symbols[index % len(self._symbols)]
            price: Any = str(self._walk_price(instrument, index))
            if "malformed" in self._inject and index == 2:
                ticks.append({"schema": "bogus", "instrument": instrument})
                continue
            if "stale" in self._inject and index == 3:
                received = event + timedelta(seconds=86_401)
            if "future_available" in self._inject and index == 4:
                available = now + timedelta(seconds=3_600)
                received = available
            if "bad_price" in self._inject and index == 5:
                price = "-1"
            if "delayed" in self._inject and index == 1:
                available = event + timedelta(seconds=120)
                received = available
            ticks.append(
                {
                    "schema": FEED_TICK_SCHEMA,
                    "instrument": instrument,
                    "event_time": event.isoformat(),
                    "available_time": available.isoformat(),
                    "received_time": received.isoformat(),
                    "price": price,
                }
            )
        return ticks


def ticks_digest(ticks: Iterable[Mapping[str, Any]]) -> str:
    """Deterministic content hash binding research output to admitted data."""
    canonical = [
        (
            str(t.get("instrument")),
            str(t.get("event_time")),
            str(t.get("available_time")),
            str(t.get("received_time")),
            str(t.get("price")),
        )
        for t in ticks
    ]
    canonical.sort()
    payload = "|".join("\x1f".join(row) for row in canonical)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
