"""Roaming watch: the watcher that cannot be stopped by one closed door.

A "hidden spirit" layer over the global market watch — with honest edges:

1. **Endpoint failover** — :class:`RoamingTransport` maps each request host
   to its *officially documented* alternate domains (``EQUIVALENT_HOSTS``)
   and walks them when one is unreachable. Only alternates published by the
   exchange itself are shipped; operators can add regional ones. An unavailable
   host cools down and becomes eligible for later requests — the watcher
   never disguises itself, rotates identities, or bypasses a deliberate
   block: it simply uses the other public doors the venue itself opened.
2. **Fastest-path ordering** — every attempt is timed; healthy endpoints
   are ranked by smoothed round-trip (EWMA), so each node always asks its
   measured-fastest door first. This is the fastest *public* path available
   to this node — never claimed to beat physical co-location, which lives
   in microseconds inside the exchange's own data centre.
3. **Coverage truth** — :func:`coverage_report` shows which instruments
   kept how many independent sources this cycle, which sources went silent
   or failed, and which instruments are down to a single lifeline.
4. **24x7 self-healing loop** — :func:`run_roaming_watch` cycles forever
   (bounded), isolating every cycle's failures so one bad minute never
   kills the watcher. Redundancy is the real unstoppability: one endpoint
   falls → another door; one exchange falls → other venues quoting the same
   instrument; one node falls → the rest of the mesh keeps watching.

Live trading remains disabled. Nothing here enters any exchange's private
systems — the spirit roams public roads only.
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .data_store import ResearchStore

# Single source of truth for plain HTTP: the watch layer's own getter.
from .global_market_watch import Transport, watch_and_ingest
from .global_market_watch import _http_get as _default_http_get
from .watch_coverage import coverage_report  # noqa: F401 - compatibility re-export

MIN_INTERVAL_SECONDS = 15.0     # consistent with the Rust bridge bounds
MAX_INTERVAL_SECONDS = 3600.0
MAX_CYCLES = 100_000
DEFAULT_COOLDOWN_SECONDS = 30.0
MAX_COOLDOWN_SECONDS = 300.0
MAX_ENDPOINT_ATTEMPTS = 3
DEFAULT_MAX_CONSECUTIVE_ERRORS = 12
MAX_CONSECUTIVE_ERRORS_LIMIT = 1000
MAX_HOST_ALIASES = 8
_EWMA_ALPHA = 0.3

#: Officially documented alternate API domains per primary host. Only
#: alternates the venue itself publishes belong here — never mirrors of
#: unknown provenance. Operators may extend for their region (e.g.
#: api1.binance.us / api2.binance.us for Binance.US users).
EQUIVALENT_HOSTS: dict[str, tuple[str, ...]] = {
    "api.bybit.com": ("api.bytick.com",),
}

_HOST_RE_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789.-")


def _valid_host(host: str) -> bool:
    return (
        isinstance(host, str)
        and 0 < len(host) <= 253
        and host == host.lower()
        and all(char in _HOST_RE_CHARS for char in host)
        and ".." not in host
        and not host.startswith(("-", "."))
        and not host.endswith(("-", "."))
    )


def _host_of(url: str) -> str:
    try:
        return str(urlsplit(url).hostname or "")
    except ValueError:
        return ""


class RoamingTransport:
    """Transport with official-alternate failover and fastest-path ranking.

    Wraps any base ``Transport``. Every request is offered to the primary
    host plus its documented alternates, ordered by smoothed round-trip:
    healthy endpoints first (fastest measured first), never-tried endpoints
    next. Endpoints with an active cooldown are skipped. Only transport-level
    unavailability (``RuntimeError``) triggers the next door. A well-formed but
    unwanted response (``ValueError``) is surfaced, not retried, because retrying a
    content problem on another host just multiplies load.

    A failing endpoint cools down for ``cooldown_seconds`` and is then
    eligible again; successful known endpoints remain preferred. At most
    ``MAX_ENDPOINT_ATTEMPTS`` doors are tried per request, so failover
    never becomes a hammer.
    """

    def __init__(
        self,
        base_transport: Transport | None = None,
        *,
        equivalent_hosts: dict[str, tuple[str, ...]] | None = None,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 1.0 <= cooldown_seconds <= MAX_COOLDOWN_SECONDS:
            raise ValueError("invalid_cooldown_seconds")
        hosts = equivalent_hosts if equivalent_hosts is not None else EQUIVALENT_HOSTS
        for primary, aliases in hosts.items():
            if not _valid_host(primary) or len(aliases) > MAX_HOST_ALIASES:
                raise ValueError("invalid_endpoint_host")
            if any(not _valid_host(alias) for alias in aliases):
                raise ValueError("invalid_endpoint_host")
            if primary in aliases:
                raise ValueError("invalid_endpoint_host")
        self._base = base_transport or _default_http_get
        self._hosts = {primary: tuple(aliases) for primary, aliases in hosts.items()}
        self._cooldown = float(cooldown_seconds)
        self._clock = clock
        self._stats: dict[str, dict[str, Any]] = {}

    def _entry(self, host: str) -> dict[str, Any]:
        return self._stats.setdefault(host, {
            "attempts": 0,
            "successes": 0,
            "failures": 0,
            "ewma_rtt_ms": None,
            "last_rtt_ms": None,
            "cooldown_until": 0.0,
            "last_error": None,
        })

    def _ordered_candidates(self, url: str) -> list[str]:
        host = _host_of(url)
        now = self._clock()
        if not host:
            return [url] if self._entry(url)["cooldown_until"] <= now else []
        group = tuple(dict.fromkeys((host,) + self._hosts.get(host, ())))

        def rank(candidate: str) -> tuple[float, float]:
            entry = self._entry(candidate)
            unknown = 1.0 if entry["ewma_rtt_ms"] is None else 0.0
            ewma = entry["ewma_rtt_ms"] if entry["ewma_rtt_ms"] is not None else 0.0
            return (unknown, ewma)

        eligible = [candidate for candidate in group
                    if self._entry(candidate)["cooldown_until"] <= now]
        ordered = sorted(eligible, key=rank)  # stable: unknowns keep declared order
        return [self._swap_host(url, candidate) for candidate in ordered]

    @staticmethod
    def _swap_host(url: str, new_host: str) -> str:
        parts = urlsplit(url)
        try:
            port = parts.port
        except ValueError:
            return url
        netloc = new_host + (f":{port}" if port is not None else "")
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

    def __call__(self, url: str, timeout: float) -> bytes:
        candidates = self._ordered_candidates(url)[:MAX_ENDPOINT_ATTEMPTS]
        if not candidates:
            raise RuntimeError("watch_endpoints_cooling_down")
        last_error: RuntimeError | None = None
        for candidate in candidates:
            host = _host_of(candidate)
            entry = self._entry(host or candidate)
            entry["attempts"] += 1
            started = self._clock()
            try:
                body = self._base(candidate, timeout)
            except RuntimeError as exc:
                entry["failures"] += 1
                entry["last_error"] = str(exc)
                entry["cooldown_until"] = self._clock() + self._cooldown
                last_error = exc
                continue
            elapsed_ms = (self._clock() - started) * 1000.0
            entry["successes"] += 1
            entry["last_rtt_ms"] = round(elapsed_ms, 3)
            previous = entry["ewma_rtt_ms"]
            entry["ewma_rtt_ms"] = (
                round(elapsed_ms, 3) if previous is None
                else round(previous * (1.0 - _EWMA_ALPHA) + elapsed_ms * _EWMA_ALPHA, 3)
            )
            entry["last_error"] = None
            entry["cooldown_until"] = 0.0
            return body
        if last_error is not None:
            raise last_error
        raise RuntimeError("watch_endpoints_cooling_down")  # defensive: no eligible attempt

    def stats(self) -> dict[str, dict[str, Any]]:
        """Per-host transparency: attempts, successes, failures, smoothed
        round-trip, cooldown state. No hidden state — the node's roaming
        behaviour is fully inspectable."""
        return {
            host: dict(entry) for host, entry in sorted(self._stats.items())
        }


# -------------------------------------------------------------------- daemon

def run_roaming_watch(
    store: ResearchStore,
    *,
    cycles: int,
    interval_seconds: float = 60.0,
    watchlist: dict[str, tuple[str, ...]] | None = None,
    allowed_providers: set[str] | frozenset[str] | None = None,
    transport: Transport | None = None,
    timeout_seconds: float = 8.0,
    max_age_seconds: float = 300.0,
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    max_consecutive_errors: int = DEFAULT_MAX_CONSECUTIVE_ERRORS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """The 24x7 roaming loop: cycle, ingest, survive, repeat.

    Every cycle runs the full fail-closed watch+ingest pipeline through a
    :class:`RoamingTransport`. A cycle that explodes is recorded and the
    loop walks on — one bad minute never kills the watcher — but
    ``max_consecutive_errors`` exceptions or cycles with no watched observations
    in a row stop it honestly (a node with no working path should surface
    that, not spin silently). Partial coverage can continue; duplicates and
    ingestion-rejected quotes still demonstrate source health. Only the
    latest 50 cycle reports and errors are retained in memory and returned.
    The ingestion kill switch still gates every quote; with it off, cycles
    run and store nothing. ``cycles`` is required and bounded: for truly
    continuous duty run this under a supervisor or the Rust bridge.
    """
    if not isinstance(cycles, int) or not 1 <= cycles <= MAX_CYCLES:
        raise ValueError("invalid_cycles")
    if not MIN_INTERVAL_SECONDS <= interval_seconds <= MAX_INTERVAL_SECONDS:
        raise ValueError("invalid_interval_seconds")
    if not 1 <= max_consecutive_errors <= MAX_CONSECUTIVE_ERRORS_LIMIT:
        raise ValueError("invalid_max_consecutive_errors")

    roaming = transport if isinstance(transport, RoamingTransport) else RoamingTransport(
        transport, cooldown_seconds=cooldown_seconds, clock=clock
    )

    totals = {"fetched": 0, "ingested": 0, "duplicates_skipped": 0}
    # Keep memory bounded for supervised runs, not just the returned JSON.
    cycle_errors: deque[dict[str, str]] = deque(maxlen=50)
    cycle_reports: deque[dict[str, Any]] = deque(maxlen=50)
    latest_coverage: dict[str, Any] | None = None
    consecutive_errors = 0
    cycles_run = 0
    stop_reason = "cycles_completed"
    started = clock()

    for cycle in range(cycles):
        try:
            summary = watch_and_ingest(
                store,
                allowed_providers=allowed_providers,
                watchlist=watchlist,
                transport=roaming,
                timeout_seconds=timeout_seconds,
                max_age_seconds=max_age_seconds,
            )
            totals["fetched"] += summary["fetched"]
            totals["ingested"] += summary["ingested"]
            totals["duplicates_skipped"] += summary["duplicates_skipped"]
            latest_coverage = summary["coverage"]
            no_observations = (
                latest_coverage["sources_watched"] > 0
                and latest_coverage["instruments_covered"] == 0
            )
            degraded = bool(
                latest_coverage["failed_sources"] or latest_coverage["silent_sources"]
                or latest_coverage["missing_instruments"]
            )
            status = (
                "no_observations" if no_observations
                else "idle" if latest_coverage["sources_watched"] == 0
                else "degraded" if degraded else "ok"
            )
            cycle_reports.append({
                "cycle": cycle,
                "status": status,
                "fetched": summary["fetched"],
                "ingested": summary["ingested"],
                "duplicates_skipped": summary["duplicates_skipped"],
                "source_errors": summary["source_errors"],
                "rejected": summary["rejected"],
                "coverage": latest_coverage,
            })
            if no_observations:
                consecutive_errors += 1
                cycle_errors.append({"cycle": str(cycle), "error": "watch_no_observations"})
            else:
                # Duplicates and ingestion-gated quotes still prove source health.
                consecutive_errors = 0
        except Exception as exc:  # noqa: BLE001 — the spirit survives its own cycle
            consecutive_errors += 1
            cycle_errors.append({"cycle": str(cycle), "error": f"{type(exc).__name__}:{exc}"})
            latest_coverage = None
            cycle_reports.append({
                "cycle": cycle, "status": "error",
                "error": f"{type(exc).__name__}:{exc}", "coverage": None,
            })
        cycles_run += 1
        if consecutive_errors >= max_consecutive_errors:
            stop_reason = "consecutive_errors"
            break
        if cycle < cycles - 1:
            sleep(interval_seconds)

    return {
        "cycle": "roaming_watch",
        "cycles_run": cycles_run,
        "cycles_requested": cycles,
        "interval_seconds": interval_seconds,
        "stop_reason": stop_reason,
        "consecutive_errors_at_stop": consecutive_errors,
        "cycle_errors": list(cycle_errors),
        "cycle_reports": list(cycle_reports),
        "latest_coverage": latest_coverage,
        "total_fetched": totals["fetched"],
        "total_ingested": totals["ingested"],
        "total_duplicates_skipped": totals["duplicates_skipped"],
        "endpoint_stats": roaming.stats(),
        "elapsed_seconds": round(clock() - started, 3),
        "co_location": False,
        "live_execution": False,
        "broker_order_submitted": False,
    }
