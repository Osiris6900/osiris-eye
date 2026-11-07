"""Stream fleet: many spirits per exchange, every one of them a welcome guest.

One stream session is one listener. The fleet runs **several independent
sessions per venue** — the spirits the operator asked for — each with its
own connection, its own stats, its own reconnect budget, so one dropped
wire never silences a venue. Sessions are bounded; the fleet loops them,
so the spirits stand watch 24x7, respawning honestly when a session ends.

Honest edges, unchanged from the rest of the watch layer:

- **"Inside the exchange" means the venue's own public doorway.** Each
  spirit holds one persistent public WebSocket connection — a live wire
  into the venue's data centre that the venue itself opened. Nothing
  enters private systems; nothing is disguised.
- **Spirits are guests, not a crowd.** ``spirits_per_venue`` is capped at
  4 — far inside venue limits (e.g. Binance allows 300 connection
  attempts per 5 minutes per IP) — and spirit starts are staggered so the
  fleet never knocks on all doors in the same instant.
- **Redundancy is measured, not assumed.** Two spirits receiving the same
  tick produce the same signed payload; the second is counted in
  ``duplicates_skipped``. That count *is* the proof the spirits agree.
- **Push beats poll; neither beats co-location.** The venue publishes the
  tick, then the spirits receive it. Co-located systems still read the
  matching engine first — in microseconds. No claim otherwise.
- **Mesh scale is the real multiplication.** One node runs up to 4
  spirits per venue; every additional mesh node runs its own fleet. That
  is how "many spirits in every exchange" honestly grows.

Live trading remains disabled.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from .data_store import ResearchStore
from .global_market_watch import _now_utc
from .stream_watch import (
    DEFAULT_EMIT_INTERVAL_SECONDS,
    DEFAULT_INACTIVITY_SECONDS,
    DEFAULT_MAX_MESSAGES,
    DEFAULT_MAX_RECONNECTS,
    DEFAULT_STREAM_WATCHLIST,
    MAX_EMIT_INTERVAL_SECONDS,
    MAX_INACTIVITY_SECONDS,
    MAX_MESSAGES,
    MAX_RECONNECTS_LIMIT,
    MAX_STREAM_SECONDS,
    MIN_EMIT_INTERVAL_SECONDS,
    MIN_INACTIVITY_SECONDS,
    MIN_MESSAGES,
    MIN_STREAM_SECONDS,
    STREAM_PROVIDERS,
    _default_connect,
    _run_venue,
)
from .watch_coverage import coverage_report

MIN_SPIRITS_PER_VENUE = 1
#: Honest guest cap: venue limits sit orders of magnitude higher (Binance:
#: 300 connection attempts per 5 minutes per IP); the fleet stays a
#: polite guest, never a crowd at the door.
MAX_SPIRITS_PER_VENUE = 4
DEFAULT_SPIRITS_PER_VENUE = 2
_SPIRIT_STAGGER_SECONDS = 0.25
_MAX_SPIRIT_STAGGER_SECONDS = 5.0
MAX_FLEET_CYCLES = 100_000
DEFAULT_MAX_CONSECUTIVE_ERRORS = 12
MAX_CONSECUTIVE_ERRORS_LIMIT = 1000


def _validate(
    *,
    spirits_per_venue: int,
    stream_seconds: float,
    cycles: int,
    max_messages: int,
    emit_interval_seconds: float,
    inactivity_seconds: float,
    max_reconnects: int,
    max_consecutive_errors: int,
    spirit_stagger_seconds: float,
) -> None:
    if not isinstance(spirits_per_venue, int) or not (
        MIN_SPIRITS_PER_VENUE <= spirits_per_venue <= MAX_SPIRITS_PER_VENUE
    ):
        raise ValueError("invalid_spirits_per_venue")
    if not MIN_STREAM_SECONDS <= stream_seconds <= MAX_STREAM_SECONDS:
        raise ValueError("invalid_stream_seconds")
    if not isinstance(cycles, int) or not 1 <= cycles <= MAX_FLEET_CYCLES:
        raise ValueError("invalid_cycles")
    if not MIN_MESSAGES <= max_messages <= MAX_MESSAGES:
        raise ValueError("invalid_max_messages")
    if not MIN_EMIT_INTERVAL_SECONDS <= emit_interval_seconds <= MAX_EMIT_INTERVAL_SECONDS:
        raise ValueError("invalid_emit_interval_seconds")
    if not MIN_INACTIVITY_SECONDS <= inactivity_seconds <= MAX_INACTIVITY_SECONDS:
        raise ValueError("invalid_inactivity_seconds")
    if not 0 <= max_reconnects <= MAX_RECONNECTS_LIMIT:
        raise ValueError("invalid_max_reconnects")
    if not 1 <= max_consecutive_errors <= MAX_CONSECUTIVE_ERRORS_LIMIT:
        raise ValueError("invalid_max_consecutive_errors")
    if not 0.0 <= spirit_stagger_seconds <= _MAX_SPIRIT_STAGGER_SECONDS:
        raise ValueError("invalid_spirit_stagger_seconds")


async def _run_spirit(
    provider: str,
    instruments: tuple[str, ...],
    spirit_index: int,
    *,
    stagger_seconds: float,
    sleep: Callable[[float], Any],
    **session_kwargs: Any,
) -> dict[str, Any]:
    """One spirit: a staggered start, then one bounded venue session.

    The stagger keeps the fleet polite — spirits never knock on every
    door in the same instant — and costs the first spirit nothing.
    """
    if spirit_index and stagger_seconds:
        await sleep(stagger_seconds * spirit_index)
    stats = await _run_venue(provider, instruments, sleep=sleep, **session_kwargs)
    stats["spirit"] = f"{provider}#spirit-{spirit_index}"
    return stats


async def _run_fleet_session(
    watchlist: dict[str, tuple[str, ...]],
    *,
    spirits_per_venue: int,
    stream_seconds: float,
    spirit_stagger_seconds: float,
    clock: Callable[[], float],
    sleep: Callable[[float], Any],
    **session_kwargs: Any,
) -> list[dict[str, Any]]:
    """One fleet-wide session: every spirit on every venue, concurrently."""
    deadline = clock() + stream_seconds
    tasks = [
        _run_spirit(
            provider,
            tuple(instruments),
            spirit_index,
            stagger_seconds=spirit_stagger_seconds,
            sleep=sleep,
            deadline=deadline,
            clock=clock,
            **session_kwargs,
        )
        for provider, instruments in sorted(watchlist.items())
        for spirit_index in range(spirits_per_venue)
    ]
    return await asyncio.gather(*tasks)


def run_stream_fleet(
    store: ResearchStore,
    *,
    stream_seconds: float,
    spirits_per_venue: int = DEFAULT_SPIRITS_PER_VENUE,
    cycles: int = 1,
    watchlist: dict[str, tuple[str, ...]] | None = None,
    allowed_providers: set[str] | frozenset[str] | None = None,
    max_age_seconds: float = 300.0,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    emit_interval_seconds: float = DEFAULT_EMIT_INTERVAL_SECONDS,
    inactivity_seconds: float = DEFAULT_INACTIVITY_SECONDS,
    max_reconnects: int = DEFAULT_MAX_RECONNECTS,
    max_consecutive_errors: int = DEFAULT_MAX_CONSECUTIVE_ERRORS,
    spirit_stagger_seconds: float = _SPIRIT_STAGGER_SECONDS,
    connect: Callable[[str], Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Any] | None = None,
    now_fn: Callable[[], datetime] = _now_utc,
) -> dict[str, Any]:
    """The 24x7 fleet: many spirits per venue, sessions respawning forever.

    Each cycle opens ``spirits_per_venue`` independent public stream
    sessions per watched venue, all concurrent, all feeding the same
    fail-closed ingestion gates. A cycle that explodes is recorded and
    the fleet walks on; ``max_consecutive_errors`` blow-ups in a row stop
    it honestly. ``cycles`` bounds the duty: for continuous watch, run
    under a supervisor — like every other loop in this project.
    """
    _validate(
        spirits_per_venue=spirits_per_venue,
        stream_seconds=stream_seconds,
        cycles=cycles,
        max_messages=max_messages,
        emit_interval_seconds=emit_interval_seconds,
        inactivity_seconds=inactivity_seconds,
        max_reconnects=max_reconnects,
        max_consecutive_errors=max_consecutive_errors,
        spirit_stagger_seconds=spirit_stagger_seconds,
    )
    watchlist = DEFAULT_STREAM_WATCHLIST if watchlist is None else watchlist
    if not watchlist:
        raise ValueError("empty_stream_watchlist")
    unknown = set(watchlist) - STREAM_PROVIDERS
    if unknown:
        raise ValueError("unknown_stream_source:" + ",".join(sorted(unknown)))
    providers = set(allowed_providers) if allowed_providers is not None else set(watchlist)

    totals = {
        key: 0
        for key in (
            "connections",
            "frames",
            "quotes_parsed",
            "quotes_emitted",
            "quotes_throttled",
            "ingested",
            "duplicates_skipped",
            "rejected",
            "parse_errors",
            "reconnects",
        )
    }
    cycle_errors: list[dict[str, str]] = []
    consecutive_errors = 0
    cycles_run = 0
    stop_reason = "cycles_completed"
    last_spirits: dict[str, dict[str, Any]] = {}
    last_coverage: dict[str, Any] = {}
    started = clock()

    for cycle in range(cycles):
        try:
            spirit_stats = asyncio.run(
                _run_fleet_session(
                    watchlist,
                    spirits_per_venue=spirits_per_venue,
                    stream_seconds=stream_seconds,
                    spirit_stagger_seconds=spirit_stagger_seconds,
                    clock=clock,
                    sleep=sleep or asyncio.sleep,
                    store=store,
                    allowed_providers=providers,
                    max_age_seconds=max_age_seconds,
                    max_messages=max_messages,
                    emit_interval=emit_interval_seconds,
                    inactivity_seconds=inactivity_seconds,
                    max_reconnects=max_reconnects,
                    connect=connect or _default_connect,
                    now_fn=now_fn,
                )
            )
            for key in totals:
                totals[key] += sum(item[key] for item in spirit_stats)
            observations = [obs for item in spirit_stats for obs in item["emitted_observations"]]
            # Coverage keys sources by provider: a venue counts as failed
            # only when every one of its spirits ended the session errored.
            errors = []
            for provider in watchlist:
                provider_stats = [item for item in spirit_stats if item["provider"] == provider]
                if provider_stats and all(item["last_error"] for item in provider_stats):
                    errors.append(
                        {
                            "source": provider,
                            "error": "; ".join(str(item["last_error"]) for item in provider_stats),
                        }
                    )
            last_coverage = coverage_report(
                watchlist,
                {
                    "observations": observations,
                    "errors": errors,
                },
            )
            last_spirits = {}
            for item in spirit_stats:
                item.pop("emitted_observations")
                last_spirits[item["spirit"]] = item
            consecutive_errors = 0
        except Exception as exc:  # noqa: BLE001 — the fleet survives its own cycle
            consecutive_errors += 1
            cycle_errors.append({"cycle": str(cycle), "error": f"{type(exc).__name__}:{exc}"})
            if consecutive_errors >= max_consecutive_errors:
                stop_reason = "consecutive_errors"
                cycles_run += 1
                break
        cycles_run += 1

    return {
        "cycle": "stream_fleet",
        "cycles_run": cycles_run,
        "cycles_requested": cycles,
        "stop_reason": stop_reason,
        "consecutive_errors_at_stop": consecutive_errors,
        "cycle_errors": cycle_errors[-50:],
        "spirits_per_venue": spirits_per_venue,
        "spirits_total": len(watchlist) * spirits_per_venue,
        "stream_seconds": stream_seconds,
        "last_session_spirits": last_spirits,
        "totals": totals,
        "coverage": last_coverage,
        "elapsed_seconds": round(clock() - started, 3),
        "push_stream": True,
        "co_location": False,
        "live_execution": False,
        "broker_order_submitted": False,
    }
