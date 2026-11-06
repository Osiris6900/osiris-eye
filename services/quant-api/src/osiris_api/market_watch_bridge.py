"""Autonomous bridge: wire the global market watch into a P2P node's stdin.

This closes the loop: a node runs the watch cycle on an interval, writes
each observation as one JSON line to the node process's stdin (the node's
existing ingestion path), and the mesh distributes it — no operator action
per cycle. Optionally mirrors every observation into the local research
store through the fail-closed ingestion gates, so consensus and research
(``mesh_market_intelligence``) can run on top.

Fail-closed: broadcasting requires ``OSIRIS_MARKET_WATCH_BROADCAST=1``;
interval and cycle bounds are validated; source failures are isolated by
the watch layer. Live trading remains disabled.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any, Callable

from .data_store import ResearchStore
from .global_market_watch import (
    DEFAULT_PROVIDERS,
    Transport,
    _http_get,
    collect_market_observations,
)
from .p2p_ingestion import IngestionRejection, ingest_p2p_observation

ENABLE_ENV = "OSIRIS_MARKET_WATCH_BROADCAST"
MIN_INTERVAL_SECONDS = 15
MAX_INTERVAL_SECONDS = 3600
MAX_CYCLES = 100_000

WriteLine = Callable[[str], None]
SleepFn = Callable[[float], None]


class BridgeRefused(ValueError):
    """Raised when the bridge is asked to run without its explicit gates."""


def broadcast_cycle(
    write_line: WriteLine,
    *,
    store: ResearchStore | None = None,
    watchlist: dict[str, tuple[str, ...]] | None = None,
    transport: Transport = _http_get,
    timeout_seconds: float = 8.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One cycle: collect world-market observations and push each to the
    node (one JSON line), optionally mirroring into the research store."""
    if os.getenv(ENABLE_ENV) != "1":
        raise BridgeRefused("broadcast_disabled_kill_switch")

    collected = collect_market_observations(
        watchlist=watchlist, transport=transport,
        timeout_seconds=timeout_seconds, now=now,
    )
    broadcast = 0
    for observation in collected["observations"]:
        write_line(json.dumps(observation, separators=(",", ":"), sort_keys=True))
        broadcast += 1

    ingested = 0
    duplicates = 0
    rejected = 0
    if store is not None:
        for observation in collected["observations"]:
            try:
                ingest_p2p_observation(
                    store, observation,
                    allowed_providers=set(DEFAULT_PROVIDERS),
                    now=now,
                )
                ingested += 1
            except IngestionRejection as exc:
                if str(exc).startswith("store_rejected:duplicate"):
                    duplicates += 1
                else:
                    rejected += 1

    return {
        "broadcast": broadcast,
        "source_errors": collected["errors"],
        "ingested_local": ingested,
        "duplicates_skipped": duplicates,
        "rejected_local": rejected,
        "live_execution": False,
    }


def run_bridge(
    write_line: WriteLine,
    *,
    store: ResearchStore | None = None,
    watchlist: dict[str, tuple[str, ...]] | None = None,
    transport: Transport = _http_get,
    interval_seconds: float = 60.0,
    max_cycles: int = 1,
    timeout_seconds: float = 8.0,
    sleep: SleepFn = time.sleep,
) -> dict[str, Any]:
    """Run the autonomous loop: ``max_cycles`` cycles, ``interval_seconds``
    apart. Every bound is validated up front; nothing runs unbounded by
    accident."""
    if os.getenv(ENABLE_ENV) != "1":
        raise BridgeRefused("broadcast_disabled_kill_switch")
    if not MIN_INTERVAL_SECONDS <= interval_seconds <= MAX_INTERVAL_SECONDS:
        raise BridgeRefused("interval_out_of_bounds")
    if not 1 <= max_cycles <= MAX_CYCLES:
        raise BridgeRefused("max_cycles_out_of_bounds")

    summaries = []
    for _ in range(max_cycles):
        summaries.append(broadcast_cycle(
            write_line,
            store=store, watchlist=watchlist, transport=transport,
            timeout_seconds=timeout_seconds,
        ))
        sleep(interval_seconds)
    return {
        "cycles": len(summaries),
        "interval_seconds": interval_seconds,
        "summaries": summaries,
        "live_execution": False,
    }
