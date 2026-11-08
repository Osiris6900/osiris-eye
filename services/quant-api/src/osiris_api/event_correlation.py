"""Cross-layer event correlation: one real thing, many senses.

The network watches the world through many layers at once — world
events (EONET storms, USGS quakes), city weather, space weather, ocean
readings, marine alerts, frontier senses. The *same* physical event
often shows up in several layers at the same time: a hurricane appears
as an EONET storm, an NWS marine alert and a buoy wave spike.

This module clusters recent stored events that plausibly describe the
same thing (kind affinity + coarse geographic proximity + time
overlap) and reports per-cluster corroboration: how many independent
layers saw it. Corroboration raises confidence that something real
happened — it says nothing about market impact by itself; that stays
with the impact and ledger layers. Report-only: this module changes no
ingestion or research behaviour, it describes. Nothing is fabricated;
events without corroboration are still reported, honestly, as
single-sightings.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from .data_store import ResearchStore
from .p2p_ingestion import DATASET
from .research_cycle import recent_events_from_store

#: Kind affinity groups: events whose kinds share a group can be
#: sightings of the same physical event. Kinds in different groups are
#: never merged (an eclipse is not a hurricane).
KIND_AFFINITY: dict[str, tuple[str, ...]] = {
    "tropical_system": (
        "severestorms", "marine_alert", "jma_bulletin",
        "buoy_conditions", "marine_conditions", "water_level", "weather",
    ),
    "seismic": ("earthquake", "marine_alert", "jma_bulletin"),
    "volcanic": ("volcanoes", "volcano_report", "jma_bulletin"),
    "fire": ("wildfires",),
    "ice": ("sealakeice",),
    "space_weather": ("geomagnetic", "solar_wind", "xray_flare"),
}

#: Coarse geo grid (degrees). Events within the same grid cell count as
#: geographically close; events without coordinates match on time only.
GEO_CELL_DEGREES = 5.0
DEFAULT_WINDOW_HOURS = 24
MAX_WINDOW_HOURS = 24 * 7
MAX_CLUSTER_EVENTS = 200
MAX_CLUSTERS = 64


def _parse_instant(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _coordinates(event: dict[str, Any]) -> tuple[float, float] | None:
    try:
        lat = float(event.get("latitude"))
        lon = float(event.get("longitude"))
    except (TypeError, ValueError, OverflowError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return lat, lon


def _affinity_groups(kind: str) -> list[str]:
    return [name for name, kinds in KIND_AFFINITY.items() if kind in kinds]


def cluster_events(
    events: list[dict[str, Any]],
    *,
    window_hours: int = DEFAULT_WINDOW_HOURS,
) -> list[dict[str, Any]]:
    """Group events that plausibly describe the same physical thing.

    Two events join a cluster when they share a kind-affinity group,
    sit in the same coarse geo cell (when both carry coordinates), and
    their event_times fall inside ``window_hours`` of each other.
    Union-find over events; deterministic ordering. Confidence is
    ``layers_seen / layers_possible`` for the affinity group — honest
    corroboration, not probability.
    """
    if not 1 <= window_hours <= MAX_WINDOW_HOURS:
        raise ValueError("invalid_window_hours")
    if not isinstance(events, list):
        raise ValueError("invalid_events")
    events = events[:MAX_CLUSTER_EVENTS]

    prepared: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind", "")).strip().lower()
        groups = _affinity_groups(kind)
        when = _parse_instant(event.get("event_time"))
        if not kind or not groups or when is None:
            continue
        prepared.append({
            "event": event,
            "kind": kind,
            "groups": groups,
            "when": when,
            "coords": _coordinates(event),
        })

    parent = list(range(len(prepared)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    window_seconds = window_hours * 3600.0
    for i in range(len(prepared)):
        for j in range(i + 1, len(prepared)):
            first, second = prepared[i], prepared[j]
            if not set(first["groups"]) & set(second["groups"]):
                continue
            if abs((first["when"] - second["when"]).total_seconds()) > window_seconds:
                continue
            if first["coords"] and second["coords"]:
                # Distance-based proximity, not grid cells — neighbouring
                # events across a cell boundary still merge.
                if abs(first["coords"][0] - second["coords"][0]) > GEO_CELL_DEGREES:
                    continue
                if abs(first["coords"][1] - second["coords"][1]) > GEO_CELL_DEGREES:
                    continue
            union(i, j)

    clusters_map: dict[int, list[int]] = {}
    for index in range(len(prepared)):
        clusters_map.setdefault(find(index), []).append(index)

    clusters: list[dict[str, Any]] = []
    for members in clusters_map.values():
        kinds = sorted({prepared[index]["kind"] for index in members})
        groups = sorted({group for index in members for group in prepared[index]["groups"]})
        providers = sorted({
            str(prepared[index]["event"].get("source", "unknown")) for index in members
        })
        shared_groups = set(prepared[members[0]]["groups"])
        for index in members[1:]:
            shared_groups &= set(prepared[index]["groups"])
        layers_possible = max(
            (len(KIND_AFFINITY[group]) for group in shared_groups), default=1,
        )
        whens = [prepared[index]["when"] for index in members]
        summaries = [
            str(prepared[index]["event"].get("place")
                or prepared[index]["event"].get("title")
                or prepared[index]["event"].get("city")
                or "unlabelled_event")[:240]
            for index in members
        ]
        clusters.append({
            "members": len(members),
            "kinds": kinds,
            "affinity_groups": sorted(shared_groups),
            "sources": providers,
            "layers_seen": len(kinds),
            "layers_possible": layers_possible,
            "corroboration": round(min(1.0, len(kinds) / layers_possible), 4),
            "first_seen": min(whens).isoformat(),
            "last_seen": max(whens).isoformat(),
            "summaries": summaries[:8],
            "multi_sense": len(kinds) > 1,
        })

    clusters.sort(key=lambda cluster: (-cluster["layers_seen"], cluster["first_seen"]))
    return clusters[:MAX_CLUSTERS]


def correlation_report(
    store: ResearchStore,
    *,
    dataset: str = DATASET,
    now: datetime | str | None = None,
    lookback_hours: int = 72,
    max_events: int = 100,
    window_hours: int = DEFAULT_WINDOW_HOURS,
) -> dict[str, Any]:
    """Cluster the store's recent events and report corroboration.

    Reads only the node's own immutable store (via the research cycle's
    event scan) — no network call. Multi-sense clusters are the
    well-corroborated happenings; single-sightings stay visible too,
    honestly labelled as one sense only.
    """
    events = recent_events_from_store(
        store, now=now, lookback_hours=lookback_hours,
        max_events=max_events, dataset=dataset,
    )
    clusters = cluster_events(events, window_hours=window_hours)
    multi = [cluster for cluster in clusters if cluster["multi_sense"]]
    return {
        "events_scanned": len(events),
        "clusters": len(clusters),
        "multi_sense_clusters": len(multi),
        "top_clusters": multi[:8],
        "all_clusters": clusters,
        "window_hours": window_hours,
        "honesty_note": (
            "clusters share kind affinity, coarse location and time — "
            "corroboration that something happened, never proof of cause "
            "or of market impact"
        ),
        "live_execution": False,
    }
