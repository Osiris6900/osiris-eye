"""Own-sensor watch layer: the network measures the world itself.

Every other watch layer trusts someone else's instruments — USGS
seismographs, NOAA satellites, exchange feeds. This layer lets a node
publish what its **own hardware** measured: a phone's accelerometer,
magnetometer or barometer, a Raspberry Shake, an RTL-SDR dongle —
through the same ``osiris.observation.v1`` contract and the same
fail-closed ingestion gates.

Hardware access is injected: ``readers`` maps a sensor kind to a
zero-argument callable returning the raw reading (e.g. a wrapper around
Termux:API on Android, a Raspberry Shake client, ``rtl_power`` output).
This module never touches hardware itself, so it stays fully testable —
production wires the readers, tests inject fakes.

Honesty notes baked in:
- Citizen-grade sensors are noisy; every payload says so
  (``instrument_grade: "citizen_sensor"``) and out-of-bounds readings
  are rejected, never clamped or fabricated.
- **Self-measured is not self-trusted.** A node's own reading is one
  observation; meaning only emerges when independent nodes agree through
  the existing mesh consensus. No attached sensor = no observation,
  never invented data.
- Live trading remains disabled.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Callable

from .data_store import ResearchStore
from .p2p_ingestion import IngestionRejection, ingest_p2p_observation

PROVIDER_PREFIX = "node-self"
INSTRUMENT_PREFIX = "SENSE"

MAX_NODE_LABEL_LENGTH = 32
MAX_SENSOR_READERS = 8
MAX_TITLE_LENGTH = 200

_NODE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: One sensor kind: required bounded fields, optional bounded fields,
#: unit, market channel and an honest precision note. Bounds are physical
#: sanity limits for citizen hardware — outside them the reading is
#: broken, not "interesting", and is rejected.
SENSOR_KINDS: dict[str, dict[str, Any]] = {
    "seismic": {
        "required": {"value": (0.0, 60.0)},          # acceleration magnitude m/s²
        "optional": {"x": (-60.0, 60.0), "y": (-60.0, 60.0), "z": (-60.0, 60.0)},
        "unit": "m/s^2",
        "market_channel": "infrastructure",
        "precision_note": ("phone/class citizen accelerometer; detects strong "
                           "local vibration only — not an observatory seismograph"),
    },
    "magnetic": {
        "required": {"value": (0.0, 1000.0)},        # total field µT (Earth ~25–65)
        "optional": {},
        "unit": "uT",
        "market_channel": "infrastructure",
        "precision_note": ("phone magnetometer; local anomalies dominate — "
                           "geomagnetic context still comes from NOAA"),
    },
    "pressure": {
        "required": {"value": (300.0, 1200.0)},      # barometric hPa
        "optional": {},
        "unit": "hPa",
        "market_channel": "behavioral_research",
        "precision_note": "phone barometer; weather-scale only",
    },
    "radio": {
        "required": {"frequency_mhz": (0.01, 6000.0), "strength_db": (-130.0, 10.0)},
        "optional": {},
        "unit": "MHz/dB",
        "market_channel": "behavioral_research",
        "precision_note": ("low-cost SDR receiver (e.g. RTL-SDR); relative "
                           "signal strength, not calibrated flux"),
    },
}

#: A reader returns the raw reading dict for one kind, e.g.
#: ``{"value": 0.42, "x": 0.1, "y": -0.4, "z": 9.8}`` for ``seismic``.
Reader = Callable[[], dict[str, Any]]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _validate_node_label(node_label: str) -> str:
    label = str(node_label).strip()
    if (not label or len(label) > MAX_NODE_LABEL_LENGTH
            or not _NODE_LABEL_RE.match(label)):
        raise ValueError("invalid_node_label")
    return label


def _number(raw: Any) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _validate_reading(kind: str, raw: Any) -> dict[str, float]:
    """Normalize one reader result; fail closed on anything unexpected."""
    spec = SENSOR_KINDS[kind]
    if not isinstance(raw, dict):
        raise ValueError(f"invalid_sensor_reading:{kind}")
    reading: dict[str, float] = {}
    for field, (low, high) in spec["required"].items():
        value = _number(raw.get(field))
        if value is None:
            raise ValueError(f"invalid_sensor_reading:{kind}")
        if not low <= value <= high:
            raise ValueError(f"reading_out_of_bounds:{kind}")
        reading[field] = value
    for field, (low, high) in spec["optional"].items():
        if field not in raw or raw.get(field) is None:
            continue
        value = _number(raw.get(field))
        if value is None or not low <= value <= high:
            raise ValueError(f"reading_out_of_bounds:{kind}")
        reading[field] = value
    return reading


def _validate_readers(readers: dict[str, Reader]) -> dict[str, Reader]:
    if not isinstance(readers, dict) or not readers:
        raise ValueError("no_sensor_readers")
    if len(readers) > MAX_SENSOR_READERS:
        raise ValueError("too_many_sensor_readers")
    for kind, reader in readers.items():
        if kind not in SENSOR_KINDS:
            raise ValueError(f"unknown_sensor_kind:{kind}")
        if not callable(reader):
            raise ValueError(f"invalid_sensor_reader:{kind}")
    return readers


def _observation(kind: str, label: str, reading: dict[str, float],
                 now: datetime) -> dict[str, Any]:
    spec = SENSOR_KINDS[kind]
    headline = ", ".join(f"{field} {value:g}" for field, value in sorted(reading.items()))
    event = {
        "kind": f"sensor_{kind}",
        "sensor_kind": kind,
        "reading": reading,
        "unit": spec["unit"],
        "event_time": now.isoformat(),
        "title": (f"Self-measured {kind}: {headline} {spec['unit']} "
                  f"(node {label})")[:MAX_TITLE_LENGTH],
        "measurement": "self_measured",
        "instrument_grade": "citizen_sensor",
        "market_channel": spec["market_channel"],
        "source": "own_sensor_watch",
        "precision_note": spec["precision_note"],
    }
    payload = json.dumps(event, allow_nan=False, separators=(",", ":"), sort_keys=True)
    return {
        "schema": "osiris.observation.v1",
        "instrument": f"{INSTRUMENT_PREFIX}:{kind.upper()}:{label}",
        "observed_at": now.isoformat(),
        "provider": f"{PROVIDER_PREFIX}:{label}",
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


def collect_sensor_observations(
    *,
    readers: dict[str, Reader],
    node_label: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read every attached sensor once and build observations.

    Reader failures and invalid readings are collected as errors, never
    fatal and never replaced with invented values. With no readers
    configured the call refuses — a node without sensors simply does not
    publish this layer.
    """
    label = _validate_node_label(node_label)
    active = _validate_readers(readers)
    current = now or _now_utc()

    observations: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for kind in sorted(active):
        try:
            reading = _validate_reading(kind, active[kind]())
        except (ValueError, RuntimeError, OSError) as exc:
            errors.append({"sensor": kind, "error": str(exc)})
            continue
        except Exception as exc:  # a broken driver must not kill the cycle
            errors.append({"sensor": kind, "error": f"reader_failed:{exc}"[:120]})
            continue
        observations.append(_observation(kind, label, reading, current))
    return {
        "observations": observations,
        "errors": errors,
        "collected_at": current.isoformat(),
        "live_execution": False,
    }


def watch_and_ingest_sensors(
    store: ResearchStore,
    *,
    readers: dict[str, Reader],
    node_label: str,
    allowed_providers: set[str] | frozenset[str] | None = None,
    max_age_seconds: float = 300.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One own-sensor cycle: measure locally, then run every observation
    through the fail-closed ingestion gates.

    The default allowlist is exactly this node's provider — trusting
    *other* nodes' self-measured data is the operator's explicit choice,
    and even then the mesh quorum gates decide.
    """
    label = _validate_node_label(node_label)
    current = now or _now_utc()
    collected = collect_sensor_observations(
        readers=readers, node_label=label, now=current,
    )
    providers = (set(allowed_providers) if allowed_providers is not None
                 else {f"{PROVIDER_PREFIX}:{label}"})

    ingested = 0
    duplicates = 0
    rejected: list[dict[str, str]] = []
    for observation in collected["observations"]:
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
                    "instrument": str(observation.get("instrument", "?")),
                    "reason": reason,
                })

    return {
        "cycle": "own_sensor_watch",
        "node_label": label,
        "fetched": len(collected["observations"]),
        "ingested": ingested,
        "duplicates_skipped": duplicates,
        "rejected": rejected,
        "sensor_errors": collected["errors"],
        "collected_at": collected["collected_at"],
        "live_execution": False,
        "broker_order_submitted": False,
    }
