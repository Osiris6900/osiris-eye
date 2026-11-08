"""Ocean watch layer: the network watches the world's seas, end to end.

Polls keyless public ocean-signal endpoints (no API key, no signup, no
payment) and normalizes each reading into the signed
``osiris.observation.v1`` contract for the authorized ingestion pipeline
(issue #87), so any node can watch the seas and share what it sees over
the mesh:

- Open-Meteo Marine API — live wave height/period, sea-surface
  temperature and ocean-current velocity at the world's major shipping
  chokepoints (Malacca, Hormuz, Suez, Panama, Bab el-Mandeb, English
  Channel, Cape of Good Hope, Gibraltar).
- NOAA NDBC realtime2 buoy files — newest standard meteorological
  observation per offshore buoy (wind, gust, wave height, pressure,
  water temperature).
- NOAA CO-OPS tides & currents — latest water level at reference tide
  stations (The Battery NY, San Francisco, Key West).
- NWS api.weather.gov — active US marine/coastal alerts (tsunami,
  hurricane, storm, coastal flood, special marine).

The companion ``event_market_impact`` module then checks each ocean
event's plausibly exposed instruments for unusual moves.

Timing semantics: ``observed_at`` is the moment the network collected
the reading (freshness gates therefore mean "the mesh saw this
recently"); the reading's own measurement time stays inside the
payload. Nothing is backdated, nothing is fabricated.

Fail-closed: unreachable or malformed sources are recorded as errors;
no reading is ever invented. Live trading remains disabled.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .data_store import ResearchStore
from .p2p_ingestion import IngestionRejection, ingest_p2p_observation

PROVIDER_MARINE = "open-meteo-marine"
PROVIDER_NDBC = "noaa-ndbc"
PROVIDER_COOPS = "noaa-coops"
PROVIDER_NWS = "nws-alerts"

DEFAULT_PROVIDERS = frozenset({PROVIDER_MARINE, PROVIDER_NDBC, PROVIDER_COOPS, PROVIDER_NWS})

#: Keyless default ocean watchlist. Operators can extend it; every
#: source here needs no account, key or payment.
DEFAULT_OCEAN_WATCHLIST: dict[str, tuple[str, ...]] = {
    PROVIDER_MARINE: (
        "malacca", "hormuz", "suez", "panama", "bab-el-mandeb",
        "english-channel", "cape-of-good-hope", "gibraltar",
    ),
    PROVIDER_NDBC: ("46059", "51001", "42001", "46002"),
    PROVIDER_COOPS: ("8518750", "9414290", "8724580"),
    PROVIDER_NWS: (
        "Tsunami Warning", "Tsunami Advisory", "Hurricane Warning",
        "Storm Warning", "Coastal Flood Warning", "Special Marine Warning",
    ),
}

#: Shipping chokepoints: name → (latitude, longitude). These are the
#: sea lanes world trade physically passes through; marine conditions
#: here are what global shipping actually feels.
DEFAULT_MARINE_SITES: dict[str, tuple[float, float]] = {
    "malacca": (1.26, 103.82),
    "hormuz": (26.57, 56.25),
    "suez": (30.00, 32.55),
    "panama": (9.08, -79.68),
    "bab-el-mandeb": (12.58, 43.33),
    "english-channel": (50.20, 0.50),
    "cape-of-good-hope": (-34.83, 19.98),
    "gibraltar": (35.97, -5.49),
}

#: Buoys worth watching offshore: station → descriptive label.
DEFAULT_BUOY_LABELS: dict[str, str] = {
    "46059": "California offshore buoy 46059",
    "51001": "Hawaii northwest buoy 51001",
    "42001": "Gulf of Mexico buoy 42001",
    "46002": "Oregon offshore buoy 46002",
}

#: Tide stations worth watching: id → descriptive label.
DEFAULT_TIDE_LABELS: dict[str, str] = {
    "8518750": "The Battery, New York",
    "9414290": "San Francisco, California",
    "8724580": "Key West, Florida",
}

#: NWS event names this layer knows how to ask for.
KNOWN_NWS_EVENTS = frozenset({
    "Tsunami Warning", "Tsunami Advisory", "Tsunami Watch",
    "Hurricane Warning", "Hurricane Watch", "Tropical Storm Warning",
    "Tropical Storm Watch", "Storm Warning", "Storm Watch",
    "Coastal Flood Warning", "Coastal Flood Advisory", "Coastal Flood Watch",
    "Special Marine Warning", "High Surf Warning", "High Surf Advisory",
})

MAX_EVENTS_PER_SOURCE = 100
MAX_RESPONSE_BYTES = 2 * 1_048_576
MAX_TEXT_LENGTH = 240
NWS_ALERTS_PER_EVENT = 25

_SITE_SLUG = re.compile(r"[a-z0-9-]{2,40}")
_STATION_ID = re.compile(r"[A-Za-z0-9]{4,8}")
_TIDE_STATION = re.compile(r"[0-9]{7}")

Transport = Callable[[str, float], bytes]


def _http_get(url: str, timeout: float) -> bytes:
    request = Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "Osiris-Eye/0.3 ocean-watch",
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("watch_source_unavailable") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("watch_response_too_large")
    return body


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    return text[:MAX_TEXT_LENGTH]


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _bounded(value: Any, low: float, high: float) -> float | None:
    number = _number(value)
    if number is None or not low <= number <= high:
        return None
    return number


def _iso_text(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).isoformat()


def _observation(instrument: str, provider: str, event: dict[str, Any], now: datetime) -> dict[str, Any]:
    payload = json.dumps(event, allow_nan=False, separators=(",", ":"), sort_keys=True)
    return {
        "schema": "osiris.observation.v1",
        "instrument": instrument,
        "observed_at": now.isoformat(),
        "provider": provider,
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


def _fetch_marine(sites: tuple[str, ...], transport: Transport, timeout: float,
                  now: datetime,
                  site_registry: dict[str, tuple[float, float]] | None = None
                  ) -> list[dict[str, Any]]:
    registry = site_registry or DEFAULT_MARINE_SITES
    observations: list[dict[str, Any]] = []
    for raw_site in sites:
        slug = raw_site.strip().lower()
        if not _SITE_SLUG.fullmatch(slug):
            raise ValueError("invalid_marine_site")
        if slug not in registry:
            raise ValueError("unknown_marine_site:" + slug)
        latitude, longitude = registry[slug]
        body = transport(
            "https://marine-api.open-meteo.com/v1/marine"
            f"?latitude={latitude}&longitude={longitude}"
            "&current=wave_height,wave_period,sea_surface_temperature,ocean_current_velocity"
            "&timezone=UTC",
            timeout,
        )
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_marine_response") from exc
        current = data.get("current") if isinstance(data, dict) else None
        if not isinstance(current, dict):
            raise ValueError("invalid_marine_response")
        wave_height = _bounded(current.get("wave_height"), 0.0, 40.0)
        wave_period = _bounded(current.get("wave_period"), 0.0, 30.0)
        sea_temp = _bounded(current.get("sea_surface_temperature"), -3.0, 45.0)
        current_velocity = _bounded(current.get("ocean_current_velocity"), 0.0, 15.0)
        if wave_height is None and sea_temp is None:
            raise ValueError("invalid_marine_response")
        event = {
            "kind": "marine_conditions",
            "place": f"{slug} shipping chokepoint",
            "site": slug,
            "latitude": latitude,
            "longitude": longitude,
            "wave_height_m": wave_height,
            "wave_period_s": wave_period,
            "sea_surface_temperature_c": sea_temp,
            "ocean_current_velocity_kmh": current_velocity,
            "event_time": _iso_text(current.get("time")),
            "source": "open_meteo_marine",
        }
        observations.append(_observation(f"SEA:{slug.upper()}", PROVIDER_MARINE, event, now))
        if len(observations) > MAX_EVENTS_PER_SOURCE:
            raise ValueError("too_many_events")
    return observations


def _fetch_ndbc(stations: tuple[str, ...], transport: Transport, timeout: float,
                now: datetime,
                buoy_labels: dict[str, str] | None = None) -> list[dict[str, Any]]:
    labels = buoy_labels or DEFAULT_BUOY_LABELS
    observations: list[dict[str, Any]] = []
    for raw_station in stations:
        station = raw_station.strip().upper()
        if not _STATION_ID.fullmatch(station):
            raise ValueError("invalid_ndbc_station")
        body = transport(
            f"https://www.ndbc.noaa.gov/data/realtime2/{station}.txt",
            timeout,
        )
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("invalid_ndbc_response") from exc
        newest: list[str] | None = None
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            fields = stripped.split()
            if len(fields) >= 16:
                newest = fields
                break  # realtime2 rows are newest-first
        if newest is None:
            raise ValueError("invalid_ndbc_response")

        def field(index: int) -> str | None:
            value = newest[index]
            return None if value == "MM" else value

        year = _number(field(0))
        month = _number(field(1))
        day = _number(field(2))
        hour = _number(field(3))
        minute = _number(field(4))
        if year is None or month is None or day is None or hour is None or minute is None:
            raise ValueError("invalid_ndbc_response")
        try:
            event_time = datetime(
                int(year), int(month), int(day), int(hour), int(minute), tzinfo=timezone.utc,
            ).isoformat()
        except ValueError as exc:
            raise ValueError("invalid_ndbc_response") from exc
        event = {
            "kind": "buoy_conditions",
            "place": labels.get(station, f"offshore buoy {station}"),
            "station": station,
            "event_time": event_time,
            "wind_speed_ms": _bounded(field(6), 0.0, 120.0),
            "wind_gust_ms": _bounded(field(7), 0.0, 150.0),
            "wave_height_m": _bounded(field(8), 0.0, 40.0),
            "pressure_hpa": _bounded(field(12), 800.0, 1100.0),
            "air_temperature_c": _bounded(field(13), -60.0, 60.0),
            "water_temperature_c": _bounded(field(14), -3.0, 45.0),
            "source": "noaa_ndbc_realtime2",
        }
        observations.append(_observation(f"BUOY:{station}", PROVIDER_NDBC, event, now))
        if len(observations) > MAX_EVENTS_PER_SOURCE:
            raise ValueError("too_many_events")
    return observations


def _fetch_coops(stations: tuple[str, ...], transport: Transport, timeout: float,
                 now: datetime,
                 tide_labels: dict[str, str] | None = None) -> list[dict[str, Any]]:
    labels = tide_labels or DEFAULT_TIDE_LABELS
    observations: list[dict[str, Any]] = []
    for raw_station in stations:
        station = raw_station.strip()
        if not _TIDE_STATION.fullmatch(station):
            raise ValueError("invalid_coops_station")
        body = transport(
            "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
            f"?product=water_level&application=OSIRIS_EYE&date=latest&station={station}"
            "&datum=MSL&time_zone=gmt&units=metric&format=json",
            timeout,
        )
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_coops_response") from exc
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise ValueError("invalid_coops_response")
        row = rows[0]
        water_level = _bounded(row.get("v"), -15.0, 15.0)
        event_time = _iso_text(str(row.get("t", "")).replace(" ", "T") + "+00:00")
        if water_level is None or event_time is None:
            raise ValueError("invalid_coops_response")
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        event = {
            "kind": "water_level",
            "place": labels.get(station, _text(metadata.get("name")) or f"tide station {station}"),
            "station": station,
            "event_time": event_time,
            "water_level_m": water_level,
            "datum": "MSL",
            "source": "noaa_coops_datagetter",
        }
        observations.append(_observation(f"TIDE:{station}", PROVIDER_COOPS, event, now))
        if len(observations) > MAX_EVENTS_PER_SOURCE:
            raise ValueError("too_many_events")
    return observations


def _fetch_nws(event_names: tuple[str, ...], transport: Transport, timeout: float,
               now: datetime) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_name in event_names:
        name = raw_name.strip()
        if name not in KNOWN_NWS_EVENTS:
            raise ValueError("invalid_nws_event:" + name)
        body = transport(
            f"https://api.weather.gov/alerts/active?event={quote(name)}",
            timeout,
        )
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_nws_response") from exc
        features = data.get("features") if isinstance(data, dict) else None
        if not isinstance(features, list):
            raise ValueError("invalid_nws_response")
        for feature in features[:NWS_ALERTS_PER_EVENT]:
            if not isinstance(feature, dict):
                continue
            properties = feature.get("properties")
            if not isinstance(properties, dict):
                continue
            alert_id = _text(properties.get("id"))
            headline = _text(properties.get("headline"))
            if not alert_id or alert_id in seen_ids:
                continue
            seen_ids.add(alert_id)
            event = {
                "kind": "marine_alert",
                "title": headline or _text(properties.get("event")) or alert_id,
                "place": _text(properties.get("areaDesc")) or None,
                "nws_id": alert_id,
                "event_name": _text(properties.get("event")) or name,
                "severity": _text(properties.get("severity")) or None,
                "certainty": _text(properties.get("certainty")) or None,
                "event_time": _iso_text(properties.get("onset")),
                "expires": _iso_text(properties.get("expires")),
                "source": "nws_weather_gov_alerts",
            }
            instrument_id = re.sub(r"[^A-Za-z0-9-]", "", alert_id)[-32:] or "UNKNOWN"
            observations.append(_observation(f"ALRT:{instrument_id.upper()}", PROVIDER_NWS, event, now))
            if len(observations) > MAX_EVENTS_PER_SOURCE:
                raise ValueError("too_many_events")
    return observations


_SOURCE_FETCHERS = {
    PROVIDER_MARINE: _fetch_marine,
    PROVIDER_NDBC: _fetch_ndbc,
    PROVIDER_COOPS: _fetch_coops,
    PROVIDER_NWS: _fetch_nws,
}


def collect_ocean_observations(
    *,
    watchlist: dict[str, tuple[str, ...]] | None = None,
    transport: Transport = _http_get,
    timeout_seconds: float = 8.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch ocean readings from every keyless source; errors are
    collected, never fatal, and no reading is ever fabricated."""
    if not 1 <= timeout_seconds <= 30:
        raise ValueError("invalid_http_timeout")
    watchlist = watchlist or DEFAULT_OCEAN_WATCHLIST
    unknown = set(watchlist) - set(_SOURCE_FETCHERS)
    if unknown:
        raise ValueError("unknown_watch_source:" + ",".join(sorted(unknown)))

    current = now or _now_utc()
    observations: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for provider, topics in sorted(watchlist.items()):
        try:
            observations.extend(
                _SOURCE_FETCHERS[provider](tuple(topics), transport, timeout_seconds, current)
            )
        except (RuntimeError, ValueError) as exc:
            errors.append({"source": provider, "error": str(exc)})
    return {
        "observations": observations,
        "errors": errors,
        "collected_at": current.isoformat(),
        "live_execution": False,
    }


def watch_and_ingest_ocean(
    store: ResearchStore,
    *,
    allowed_providers: set[str] | frozenset[str] | None = None,
    watchlist: dict[str, tuple[str, ...]] | None = None,
    transport: Transport = _http_get,
    timeout_seconds: float = 8.0,
    max_age_seconds: float = 300.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One ocean-watch cycle: fetch sea readings, then run every reading
    through the fail-closed ingestion gates into the research store.

    Duplicate readings (same reading, unchanged data) are counted and
    skipped, not stored twice; an updated reading appends as a new
    immutable record.
    """
    current = now or _now_utc()
    collected = collect_ocean_observations(
        watchlist=watchlist, transport=transport,
        timeout_seconds=timeout_seconds, now=current,
    )
    providers = set(allowed_providers) if allowed_providers is not None else set(DEFAULT_PROVIDERS)

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
        "cycle": "ocean_watch",
        "fetched": len(collected["observations"]),
        "ingested": ingested,
        "duplicates_skipped": duplicates,
        "rejected": rejected,
        "source_errors": collected["errors"],
        "collected_at": collected["collected_at"],
        "live_execution": False,
        "broker_order_submitted": False,
    }
