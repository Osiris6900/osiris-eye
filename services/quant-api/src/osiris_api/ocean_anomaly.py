"""Ocean anomaly detection: the sea itself raises the alarm.

The ocean watch layer stores raw readings (chokepoint marine conditions,
offshore buoys). This module turns the store into a detector: for every
tracked instrument it builds a baseline from its own recent history and
flags the newest reading when it is a statistical outlier (z-score vs
baseline, ``|z| >= 3`` by default) — a marine heatwave spike, an
abnormal wave field, an unusual current. Anomalies become
``osiris.observation.v1`` events (``OCEAN:`` prefix, provider
``ocean-anomaly``) so they flow through the same ingestion gates,
impact rules and research cycle as measured events.

Honest method, honestly labelled: a z-score against a short self-built
baseline is an *outlier flag*, not a climate model. Baselines need at
least 8 prior readings; instruments with less history are reported as
``baseline_building`` — never flagged on thin data. The anomaly says
"this reading is unusual for this instrument's own recent past", never
"this is dangerous". Causality is never established.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

from .data_store import ResearchStore
from .p2p_ingestion import DATASET, IngestionRejection, ingest_p2p_observation

PROVIDER_ANOMALY = "ocean-anomaly"

MAX_STORE_ROWS = 1000
MIN_BASELINE_POINTS = 8
MAX_BASELINE_POINTS = 96
DEFAULT_Z_THRESHOLD = 3.0
MAX_ANOMALIES_PER_CYCLE = 32

#: Metrics tracked per ocean event kind: payload field → (low, high).
_TRACKED_METRICS: dict[str, dict[str, tuple[float, float]]] = {
    "marine_conditions": {
        "wave_height_m": (0.0, 40.0),
        "sea_surface_temperature_c": (-3.0, 45.0),
        "ocean_current_velocity_kmh": (0.0, 15.0),
    },
    "buoy_conditions": {
        "wave_height_m": (0.0, 40.0),
        "water_temperature_c": (-3.0, 45.0),
        "wind_speed_ms": (0.0, 120.0),
    },
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_instant(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _metric_readings(store: ResearchStore, dataset: str) -> dict[tuple[str, str], list[tuple[str, float]]]:
    """(instrument, metric) → [(event_time, value)] oldest→newest.

    Reads the observations dataset newest-first, keeps ocean payloads
    with tracked metrics inside their physical bounds; rows that do not
    parse are skipped, never invented.
    """
    readings: dict[tuple[str, str], list[tuple[str, float]]] = {}
    for row in store.list(dataset, limit=MAX_STORE_ROWS):
        record_key = str(row.get("record_key", ""))
        instrument = record_key.rsplit(":", 1)[0] if ":" in record_key else ""
        if not instrument.startswith(("SEA:", "BUOY:")):
            continue
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
        if not isinstance(event, dict):
            continue
        metrics = _TRACKED_METRICS.get(str(event.get("kind", "")))
        if not metrics:
            continue
        event_time = str(event.get("event_time") or row.get("event_time") or "")
        if _parse_instant(event_time) is None:
            continue
        for metric, (low, high) in metrics.items():
            try:
                value = float(event.get(metric))
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(value) or not low <= value <= high:
                continue
            series_key = (instrument, metric)
            readings.setdefault(series_key, []).append((event_time, value))
    for series_key in readings:
        deduped = sorted(set(readings[series_key]))[-(MAX_BASELINE_POINTS + 1):]
        readings[series_key] = deduped
    return readings


def _mean_std(values: list[float]) -> tuple[float, float]:
    mean = sum(values) / len(values)
    if len(values) < 2:
        return mean, 0.0
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return mean, math.sqrt(variance)


def detect_ocean_anomalies(
    store: ResearchStore,
    *,
    dataset: str = DATASET,
    z_threshold: float = DEFAULT_Z_THRESHOLD,
    min_baseline: int = MIN_BASELINE_POINTS,
) -> dict[str, Any]:
    """Outlier flags for the newest reading of every tracked ocean metric.

    Returns per-series verdicts: ``anomaly`` (|z| >= threshold),
    ``normal``, or ``baseline_building`` (insufficient history — never
    flagged on thin data). No network call; nothing is fabricated.
    """
    if not 1.0 <= z_threshold <= 10.0:
        raise ValueError("invalid_z_threshold")
    if not 4 <= min_baseline <= MAX_BASELINE_POINTS:
        raise ValueError("invalid_min_baseline")
    readings = _metric_readings(store, dataset)

    series_reports: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    for (instrument, metric), points in sorted(readings.items()):
        if len(points) < min_baseline + 1:
            series_reports.append({
                "instrument": instrument, "metric": metric,
                "status": "baseline_building", "points": len(points),
                "required": min_baseline + 1,
            })
            continue
        baseline = [value for _, value in points[:-1]]
        latest_time, latest_value = points[-1]
        mean, std = _mean_std(baseline)
        z_score = (latest_value - mean) / std if std > 0 else 0.0
        status = "anomaly" if abs(z_score) >= z_threshold else "normal"
        report = {
            "instrument": instrument,
            "metric": metric,
            "status": status,
            "latest_value": round(latest_value, 4),
            "latest_time": latest_time,
            "baseline_mean": round(mean, 4),
            "baseline_std": round(std, 4),
            "baseline_points": len(baseline),
            "z_score": round(z_score, 4),
            "z_threshold": z_threshold,
        }
        series_reports.append(report)
        if status == "anomaly":
            anomalies.append(report)

    return {
        "series_tracked": len(readings),
        "series_reports": series_reports,
        "anomalies": anomalies[:MAX_ANOMALIES_PER_CYCLE],
        "z_threshold": z_threshold,
        "min_baseline": min_baseline,
        "method_note": (
            "z-score of the newest reading vs the instrument's own recent "
            "baseline; an outlier flag, not a climate model"
        ),
        "live_execution": False,
    }


def ingest_ocean_anomalies(
    store: ResearchStore,
    *,
    allowed_providers: set[str] | frozenset[str] | None = None,
    dataset: str = DATASET,
    z_threshold: float = DEFAULT_Z_THRESHOLD,
    min_baseline: int = MIN_BASELINE_POINTS,
    max_age_seconds: float = 300.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Detect anomalies, then ingest each one as an OCEAN: event through
    the fail-closed ingestion gates (kill switch, allowlist, freshness,
    dedupe). Unchanged anomalies are counted as duplicates, not stored
    twice."""
    current = now or _now_utc()
    report = detect_ocean_anomalies(
        store, dataset=dataset, z_threshold=z_threshold, min_baseline=min_baseline,
    )
    providers = set(allowed_providers) if allowed_providers is not None else {PROVIDER_ANOMALY}

    ingested = 0
    duplicates = 0
    rejected: list[dict[str, str]] = []
    for anomaly in report["anomalies"]:
        event = {
            "kind": "ocean_anomaly",
            "place": anomaly["instrument"],
            "instrument": anomaly["instrument"],
            "metric": anomaly["metric"],
            "event_time": anomaly["latest_time"],
            "latest_value": anomaly["latest_value"],
            "baseline_mean": anomaly["baseline_mean"],
            "baseline_std": anomaly["baseline_std"],
            "baseline_points": anomaly["baseline_points"],
            "z_score": anomaly["z_score"],
            "z_threshold": anomaly["z_threshold"],
            "source": "ocean_anomaly_detector",
        }
        payload = json.dumps(event, allow_nan=False, separators=(",", ":"), sort_keys=True)
        digest = hashlib.sha256(
            f"{anomaly['instrument']}:{anomaly['metric']}:{anomaly['latest_time']}".encode("utf-8")
        ).hexdigest()[:16].upper()
        observation = {
            "schema": "osiris.observation.v1",
            "instrument": f"OCEAN:{digest}",
            "observed_at": current.isoformat(),
            "provider": PROVIDER_ANOMALY,
            "payload": payload,
            "payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        }
        try:
            ingest_p2p_observation(
                store, observation,
                allowed_providers=providers,
                max_age_seconds=max_age_seconds,
                now=current,
            )
            ingested += 1
        except IngestionRejection as exc:
            reason = str(exc)
            if reason.startswith("store_rejected:duplicate"):
                duplicates += 1
            else:
                rejected.append({
                    "instrument": observation["instrument"],
                    "reason": reason,
                })

    return {
        "cycle": "ocean_anomaly",
        "series_tracked": report["series_tracked"],
        "anomalies_detected": len(report["anomalies"]),
        "ingested": ingested,
        "duplicates_skipped": duplicates,
        "rejected": rejected,
        "method_note": report["method_note"],
        "live_execution": False,
        "broker_order_submitted": False,
    }
