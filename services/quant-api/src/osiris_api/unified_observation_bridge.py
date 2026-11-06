"""Fail-closed unified observation bridge for market, world and celestial inputs.

All sources are independent inputs. No observation reaches the node writer
until the existing ingestion contract, provenance, freshness and kill-switch
checks succeed. This module does not launch a node or enable live trading.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Callable

from .data_store import ResearchStore
from .observation_outbox import ObservationOutbox
from .global_market_watch import (
    DEFAULT_PROVIDERS as MARKET_PROVIDERS,
    collect_market_observations,
)
from .world_event_watch import (
    DEFAULT_PROVIDERS as WORLD_PROVIDERS,
    collect_world_observations,
)
from .celestial_watch import (
    DEFAULT_PROVIDERS as CELESTIAL_PROVIDERS,
    collect_celestial_observations,
)
from .p2p_ingestion import IngestionRejection, ingest_p2p_observation

ENABLE_ENV = "OSIRIS_UNIFIED_MESH_BROADCAST"
COLLECTORS = {
    "market": (collect_market_observations, MARKET_PROVIDERS),
    "world": (collect_world_observations, WORLD_PROVIDERS),
    "celestial": (collect_celestial_observations, CELESTIAL_PROVIDERS),
}


class UnifiedBridgeRefused(ValueError):
    """The unified bridge is not authorized to publish."""


def broadcast_unified_cycle(
    write_line: Callable[[str], None],
    *,
    store: ResearchStore,
    collectors: dict[str, tuple[Callable[..., dict[str, Any]], frozenset[str]]] | None = None,
    now: datetime | None = None,
    outbox: ObservationOutbox | None = None,
) -> dict[str, Any]:
    """Collect independently and broadcast only successfully ingested records.

    Inject collectors in tests; production uses the three existing watchers.
    Ingestion kill switch remains mandatory in addition to this bridge switch.
    """
    if os.getenv(ENABLE_ENV) != "1":
        raise UnifiedBridgeRefused("unified_broadcast_disabled")
    if store is None:
        raise UnifiedBridgeRefused("validated_store_required")

    current = now or datetime.now(timezone.utc)
    active = COLLECTORS if collectors is None else collectors
    result: dict[str, Any] = {
        "broadcast": 0, "ingested": 0, "duplicates_skipped": 0,
        "rejected": 0, "source_errors": [], "live_execution": False,
        "broker_order_submitted": False,
    }
    # Validate the entire configuration before publishing the first line.
    for source, (_, providers) in active.items():
        if source not in COLLECTORS:
            raise UnifiedBridgeRefused("unknown_source:" + source)
        authorized = set(providers) & set(COLLECTORS[source][1])
        if not authorized:
            raise UnifiedBridgeRefused("no_authorized_providers:" + source)
    if outbox is not None:
        result["broadcast"] += outbox.drain(write_line)
    for source, (collect, providers) in active.items():
        authorized = set(providers) & set(COLLECTORS[source][1])
        try:
            batch = collect(now=current)
        except (ValueError, RuntimeError) as exc:
            result["source_errors"].append({"source": source, "error": str(exc)})
            continue
        result["source_errors"].extend(
            {"source": source, **err} for err in batch.get("errors", [])
        )
        for observation in batch["observations"]:
            try:
                ingest_p2p_observation(
                    store, observation, allowed_providers=authorized, now=current
                )
            except IngestionRejection as exc:
                if str(exc).startswith("store_rejected:duplicate"):
                    result["duplicates_skipped"] += 1
                else:
                    result["rejected"] += 1
                continue
            result["ingested"] += 1
            if outbox is not None:
                outbox.enqueue(observation)
                result["broadcast"] += outbox.drain(write_line)
            else:
                write_line(json.dumps(observation, separators=(",", ":"), sort_keys=True))
                result["broadcast"] += 1
    return result
