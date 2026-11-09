"""Offline market-connectivity policy for public feeds.

Reconnect delays, rate-limit cooldowns and endpoint health scores live here
so session code can be tested without a socket. This module never opens a
network connection, never reads credentials, and never authorizes an order.
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Mapping
from urllib.error import HTTPError, URLError

MIN_RETRY_AFTER_SECONDS = 1.0
MAX_RETRY_AFTER_SECONDS = 300.0
DEFAULT_RETRY_AFTER_SECONDS = 60.0
_RATE_LIMIT_STATUSES = frozenset({429, 418})


class RateLimited(RuntimeError):
    """Upstream refused the request and named a wait, already bounded.

    ``retry_after_seconds`` is always inside
    ``[MIN_RETRY_AFTER_SECONDS, MAX_RETRY_AFTER_SECONDS]``. Callers cool the
    endpoint down for that long and do not immediately try a sibling host:
    public venues often share one quota across official alternate domains.
    """

    def __init__(self, retry_after_seconds: float, *, status: int = 429) -> None:
        if type(status) is not int or status not in _RATE_LIMIT_STATUSES:
            raise ValueError("invalid_rate_limit_status")
        if isinstance(retry_after_seconds, bool) or not isinstance(
            retry_after_seconds, (int, float)
        ):
            raise ValueError("invalid_retry_after")
        if (
            not math.isfinite(retry_after_seconds)
            or retry_after_seconds < MIN_RETRY_AFTER_SECONDS
            or retry_after_seconds > MAX_RETRY_AFTER_SECONDS
        ):
            raise ValueError("invalid_retry_after")
        self.retry_after_seconds = float(retry_after_seconds)
        self.status = status
        super().__init__(f"rate_limited:{status}")


def retry_after_seconds(header: str | None, *, now: datetime | None = None) -> float:
    """Parse Retry-After into a bounded delay. Missing or garbage uses the default.

    Delta-seconds and HTTP-dates are both accepted. A zero, negative, or
    already-past delay becomes the one-second floor so a client cannot hot-loop.
    A huge delay is capped so one bad header cannot stall a feed for days.
    """
    if header is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    if not isinstance(header, str):
        return DEFAULT_RETRY_AFTER_SECONDS
    text = header.strip()
    if not text or len(text) > 128:
        return DEFAULT_RETRY_AFTER_SECONDS
    try:
        delta = float(text)
    except ValueError:
        delta = None
    if delta is not None:
        if not math.isfinite(delta):
            return DEFAULT_RETRY_AFTER_SECONDS
        return _clamp_retry(delta)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_RETRY_AFTER_SECONDS
    if when is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    if when.tzinfo is None or when.utcoffset() is None:
        when = when.replace(tzinfo=timezone.utc)
    clock = now if now is not None else datetime.now(timezone.utc)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("retry_after_now_must_be_timezone_aware")
    return _clamp_retry((when - clock.astimezone(timezone.utc)).total_seconds())


def _clamp_retry(seconds: float) -> float:
    if seconds < MIN_RETRY_AFTER_SECONDS:
        return MIN_RETRY_AFTER_SECONDS
    if seconds > MAX_RETRY_AFTER_SECONDS:
        return MAX_RETRY_AFTER_SECONDS
    return round(float(seconds), 3)


def classify_transport_failure(exc: BaseException) -> BaseException:
    """Map a socket or HTTP failure onto a fail-closed transport error.

    429 and 418 become :class:`RateLimited`. Every other transport failure
    stays a generic unavailable error so callers do not branch on upstream
    wording. Raise the returned exception ``from exc``.
    """
    if isinstance(exc, HTTPError) and exc.code in _RATE_LIMIT_STATUSES:
        header = None
        headers = getattr(exc, "headers", None)
        if headers is not None:
            header = headers.get("Retry-After")
        return RateLimited(retry_after_seconds(header), status=int(exc.code))
    if isinstance(exc, (HTTPError, URLError, TimeoutError, OSError)):
        return RuntimeError("watch_source_unavailable")
    return RuntimeError("watch_source_unavailable")


def jittered_reconnect_delay(
    attempt: int,
    *,
    material: str,
    base_seconds: float = 1.0,
    cap_seconds: float = 30.0,
) -> float:
    """Bounded exponential backoff with deterministic half-jitter.

    ``attempt`` is 1-based. The delay sits in ``[ceiling / 2, ceiling]`` where
    ``ceiling = min(cap, base * 2^(attempt - 1))``. The offset is SHA-256 of
    the venue material and the attempt, so a restart reproduces it and two
    venues do not wake on the same instant. The floor is half the ceiling,
    never zero.
    """
    if type(attempt) is not int or not 1 <= attempt <= 64:
        raise ValueError("invalid_reconnect_attempt")
    if not isinstance(material, str) or not material.strip() or len(material) > 128:
        raise ValueError("invalid_reconnect_material")
    if (
        isinstance(base_seconds, bool)
        or isinstance(cap_seconds, bool)
        or not isinstance(base_seconds, (int, float))
        or not isinstance(cap_seconds, (int, float))
        or not math.isfinite(base_seconds)
        or not math.isfinite(cap_seconds)
        or not 0 < float(base_seconds) <= float(cap_seconds) <= 300
    ):
        raise ValueError("invalid_backoff_bounds")
    ceiling = min(float(cap_seconds), float(base_seconds) * (2 ** (attempt - 1)))
    floor = ceiling / 2.0
    span_ms = int(round((ceiling - floor) * 1000.0))
    digest = hashlib.sha256(f"{material}|{attempt}|{ceiling:.3f}".encode()).digest()
    offset_ms = int.from_bytes(digest[:8], "big") % (span_ms + 1)
    return round(floor + offset_ms / 1000.0, 3)


def endpoint_health_score(entry: Mapping[str, Any]) -> float:
    """Rank a public endpoint from its own attempt history. Higher is healthier.

    Never-tried endpoints score 0.5. A known-good endpoint (at least one
    success) scores strictly above 0.5, and higher still when its success
    ratio is higher and its smoothed round-trip is lower. A known-bad
    endpoint with no successes scores 0, so failover prefers an untried
    official door over one that has only failed. Cooldown is not part of
    the score; callers exclude cooling endpoints before ranking.
    """
    successes = entry.get("successes", 0)
    failures = entry.get("failures", 0)
    if (
        isinstance(successes, bool)
        or isinstance(failures, bool)
        or not isinstance(successes, int)
        or not isinstance(failures, int)
        or successes < 0
        or failures < 0
    ):
        raise ValueError("invalid_endpoint_counters")
    if successes == 0 and failures == 0:
        return 0.5
    if successes == 0:
        return 0.0
    ratio = successes / (successes + failures)
    rtt = entry.get("ewma_rtt_ms")
    rtt_ok = (
        not isinstance(rtt, bool)
        and isinstance(rtt, (int, float))
        and math.isfinite(float(rtt))
        and float(rtt) >= 0
    )
    if not rtt_ok:
        latency = 1.0
    else:
        latency = max(1.0 / (1.0 + float(rtt) / 1000.0), 1e-3)
    rounded = round(0.5 + 0.5 * ratio * latency, 6)
    return 0.500001 if rounded <= 0.5 else rounded
