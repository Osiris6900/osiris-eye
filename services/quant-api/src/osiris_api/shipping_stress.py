"""Shipping stress index: how hard is the sea pushing back on trade?

Combines what the ocean watch layer measured at each shipping
chokepoint (wave height, ocean current) with active marine alerts into
one bounded 0–100 score per lane. One number a human can read —
"Hormuz 78, severe" — built from documented, honestly weighted
components. Missing data is reported as ``no_data``, never filled in.

Weights (documented, not fitted): wave height contributes up to 50
points (6 m+ saturates), ocean current up to 20 (3 km/h+ saturates),
an active marine alert matching the lane's region adds 30. The score
says "conditions are rough for shipping here now"; it is a context
signal for the impact layers, not a forecast and never a trading
signal.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from .data_store import ResearchStore
from .ocean_watch import DEFAULT_MARINE_SITES
from .p2p_ingestion import DATASET

MAX_STORE_ROWS = 1000
DEFAULT_FRESHNESS_MINUTES = 360  # readings older than 6h are stale
MAX_FRESHNESS_MINUTES = 24 * 60

#: Documented score components (never fitted, never hidden).
WAVE_SATURATION_M = 6.0
WAVE_MAX_POINTS = 50.0
CURRENT_SATURATION_KMH = 3.0
CURRENT_MAX_POINTS = 20.0
ALERT_POINTS = 30.0

#: Lane → alert-text keywords that count as "an alert affects this lane".
LANE_ALERT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "malacca": ("singapore", "malacca", "malaysia", "indonesia"),
    "hormuz": ("hormuz", "persian gulf", "oman", "iran", "emirates"),
    "suez": ("suez", "egypt", "red sea"),
    "panama": ("panama", "caribbean"),
    "bab-el-mandeb": ("bab el-mandeb", "bab-el-mandeb", "yemen", "aden", "red sea"),
    "english-channel": ("channel", "dover", "england", "france"),
    "cape-of-good-hope": ("good hope", "south africa", "cape town"),
    "gibraltar": ("gibraltar", "strait", "morocco", "spain"),
}

LEVELS = ((0, "calm"), (25, "moderate"), (50, "high"), (75, "severe"))


def _parse_instant(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _level(score: float) -> str:
    label = "calm"
    for threshold, name in LEVELS:
        if score >= threshold:
            label = name
    return label


def _latest_store_events(store: ResearchStore, dataset: str) -> list[dict[str, Any]]:
    """Newest-first parsed event payloads with their event_time."""
    events: list[dict[str, Any]] = []
    for row in store.list(dataset, limit=MAX_STORE_ROWS):
        stored = row.get("payload")
        if not isinstance(stored, dict):
            continue
        raw = stored.get("payload")
        if not isinstance(raw, str):
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("kind"):
            event = dict(event)
            event["_row_event_time"] = str(row.get("event_time", ""))
            events.append(event)
    return events


def shipping_stress_report(
    store: ResearchStore,
    *,
    dataset: str = DATASET,
    sites: tuple[str, ...] | None = None,
    freshness_minutes: int = DEFAULT_FRESHNESS_MINUTES,
    now: datetime | None = None,
) -> dict[str, Any]:
    """0–100 shipping stress per chokepoint lane, honestly composed.

    Per lane: the newest fresh ``marine_conditions`` reading supplies
    wave/current components; any fresh ``marine_alert``/``jma_bulletin``
    whose text names the lane's region adds the alert component. Stale
    or missing readings yield ``no_data`` — the lane is then reported
    blind, never zero-scored.
    """
    if not 30 <= freshness_minutes <= MAX_FRESHNESS_MINUTES:
        raise ValueError("invalid_freshness_minutes")
    current = now or datetime.now(timezone.utc)
    lanes = tuple(sites) if sites is not None else tuple(DEFAULT_MARINE_SITES)
    unknown = [lane for lane in lanes if lane not in DEFAULT_MARINE_SITES]
    if unknown:
        raise ValueError("unknown_lane:" + ",".join(sorted(unknown)))
    cutoff = current - timedelta(minutes=freshness_minutes)

    events = _latest_store_events(store, dataset)
    lane_reports: list[dict[str, Any]] = []
    for lane in lanes:
        reading: dict[str, Any] | None = None
        alerts = 0
        for event in events:
            kind = str(event.get("kind", ""))
            event_time = _parse_instant(event.get("event_time") or event.get("_row_event_time"))
            if event_time is None or event_time < cutoff:
                continue
            if kind == "marine_conditions" and reading is None:
                if str(event.get("site", "")) == lane:
                    reading = event
            elif kind in ("marine_alert", "jma_bulletin"):
                text = " ".join(str(event.get(key, "")) for key in ("title", "place")).lower()
                if any(keyword in text for keyword in LANE_ALERT_KEYWORDS[lane]):
                    alerts += 1
        if reading is None:
            lane_reports.append({
                "lane": lane, "status": "no_data",
                "note": "no fresh marine reading; lane reported blind, never zero-scored",
            })
            continue
        wave = reading.get("wave_height_m")
        current_vel = reading.get("ocean_current_velocity_kmh")
        wave_points = 0.0
        if isinstance(wave, (int, float)) and wave > 0:
            wave_points = min(WAVE_MAX_POINTS, (wave / WAVE_SATURATION_M) * WAVE_MAX_POINTS)
        current_points = 0.0
        if isinstance(current_vel, (int, float)) and current_vel > 0:
            current_points = min(
                CURRENT_MAX_POINTS, (current_vel / CURRENT_SATURATION_KMH) * CURRENT_MAX_POINTS,
            )
        alert_points = ALERT_POINTS if alerts > 0 else 0.0
        score = round(min(100.0, wave_points + current_points + alert_points), 1)
        lane_reports.append({
            "lane": lane,
            "status": "scored",
            "score": score,
            "level": _level(score),
            "components": {
                "wave_points": round(wave_points, 1),
                "current_points": round(current_points, 1),
                "alert_points": alert_points,
            },
            "reading": {
                "wave_height_m": wave,
                "ocean_current_velocity_kmh": current_vel,
                "event_time": reading.get("event_time"),
            },
            "active_alerts": alerts,
        })

    scored = [report for report in lane_reports if report["status"] == "scored"]
    return {
        "lanes": lane_reports,
        "scored": len(scored),
        "blind": len(lane_reports) - len(scored),
        "fleet_stress": round(
            sum(report["score"] for report in scored) / len(scored), 1,
        ) if scored else None,
        "freshness_minutes": freshness_minutes,
        "method_note": (
            "documented weights (wave ≤50, current ≤20, alert +30); a "
            "context signal about shipping conditions, never a forecast "
            "or a trading signal"
        ),
        "live_execution": False,
    }
