"""World-event watch layer: the network watches everything, not just markets.

Polls keyless public world-signal endpoints (no API key, no signup, no
payment) — USGS earthquake feeds, NASA EONET natural events (storms,
volcanoes, wildfires, ice) and Open-Meteo live weather for major financial
centres — normalizes each event into the signed ``osiris.observation.v1``
contract and feeds the authorized ingestion pipeline (issue #87), so any
node can watch world events and share them over the mesh. The companion
``event_market_impact`` module then checks each event's plausibly exposed
instruments for unusual moves.

Timing semantics: ``observed_at`` is the moment the network collected the
event (freshness gates therefore mean "the mesh saw this recently"); the
event's own occurrence time stays inside the payload. Nothing is
backdated, nothing is fabricated.

Fail-closed: unreachable or malformed sources are recorded as errors; no
event is ever invented. Live trading remains disabled.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .data_store import ResearchStore
from .p2p_ingestion import IngestionRejection, ingest_p2p_observation

PROVIDER_USGS = "usgs-earthquakes"
PROVIDER_EONET = "nasa-eonet"
PROVIDER_OPEN_METEO = "open-meteo"

DEFAULT_PROVIDERS = frozenset({PROVIDER_USGS, PROVIDER_EONET, PROVIDER_OPEN_METEO})

#: Keyless default world watchlist. Operators can extend it; every source
#: here needs no account, key or payment.
DEFAULT_WORLD_WATCHLIST: dict[str, tuple[str, ...]] = {
    PROVIDER_USGS: ("significant_day", "4.5_day"),
    PROVIDER_EONET: ("severeStorms", "volcanoes", "wildfires", "seaLakeIce", "earthquakes"),
    PROVIDER_OPEN_METEO: ("new-york", "london", "tokyo", "mumbai", "frankfurt", "singapore"),
}

#: Weather stations: major financial centres (latitude, longitude).
DEFAULT_CITIES: dict[str, tuple[float, float]] = {
    "new-york": (40.7143, -74.0060),
    "london": (51.5085, -0.1257),
    "tokyo": (35.6895, 139.6917),
    "mumbai": (19.0728, 72.8826),
    "frankfurt": (50.1155, 8.6842),
    "singapore": (1.3521, 103.8198),
}

KNOWN_EONET_CATEGORIES = frozenset({
    "drought", "dustHaze", "earthquakes", "floods", "landslides", "manmade",
    "seaLakeIce", "severeStorms", "snow", "tempExtremes", "volcanoes",
    "waterColor", "wildfires",
})

MAX_EVENTS_PER_SOURCE = 100
MAX_RESPONSE_BYTES = 2 * 1_048_576
MAX_TEXT_LENGTH = 240
EONET_EVENTS_PER_CATEGORY = 25

_USGS_FEED = re.compile(r"(significant|all|[0-9]\.[0-9])_(hour|day|week|month)")
_CITY_SLUG = re.compile(r"[a-z0-9-]{2,40}")

Transport = Callable[[str, float], bytes]


def _http_get(url: str, timeout: float) -> bytes:
    request = Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "Osiris-Eye/0.3 world-event-watch",
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


def _coordinate(value: Any, bound: float) -> float | None:
    number = _number(value)
    if number is None or abs(number) > bound:
        return None
    return number


def _iso_from_ms(epoch_ms: Any) -> str | None:
    number = _number(epoch_ms)
    if number is None:
        return None
    try:
        return datetime.fromtimestamp(number / 1000.0, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


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


def _fetch_usgs(feeds: tuple[str, ...], transport: Transport, timeout: float,
                now: datetime) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_feed in feeds:
        feed = raw_feed.strip().lower()
        if not _USGS_FEED.fullmatch(feed):
            raise ValueError("invalid_usgs_feed")
        body = transport(
            f"https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/{feed}.geojson",
            timeout,
        )
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_usgs_geojson") from exc
        features = data.get("features") if isinstance(data, dict) else None
        if not isinstance(features, list):
            raise ValueError("invalid_usgs_geojson")
        for feature in features:
            if not isinstance(feature, dict):
                continue
            properties = feature.get("properties")
            if not isinstance(properties, dict):
                continue
            event_id = _text(properties.get("id") or feature.get("id"))
            place = _text(properties.get("place"))
            event_time = _iso_from_ms(properties.get("time"))
            if not event_id or not place or event_time is None:
                continue  # event without identity/place/time is unusable; skip, never invent
            if event_id in seen_ids:
                continue  # same quake listed in overlapping feeds
            seen_ids.add(event_id)
            geometry = feature.get("geometry")
            coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
            lon = lat = depth = None
            if isinstance(coordinates, list) and len(coordinates) >= 2:
                lon = _coordinate(coordinates[0], 180.0)
                lat = _coordinate(coordinates[1], 90.0)
                depth = _number(coordinates[2]) if len(coordinates) > 2 else None
            magnitude = _number(properties.get("mag"))
            if magnitude is not None and not -5.0 <= magnitude <= 12.0:
                magnitude = None
            event = {
                "kind": "earthquake",
                "usgs_id": event_id,
                "magnitude": magnitude,
                "place": place,
                "event_time": event_time,
                "longitude": lon,
                "latitude": lat,
                "depth_km": depth,
                "tsunami_flag": bool(properties.get("tsunami")),
                "alert_level": _text(properties.get("alert")) or None,
                "source": "usgs_geojson_feed",
                "feed": feed,
            }
            observations.append(_observation(f"EQ:USGS:{event_id.upper()}", PROVIDER_USGS, event, now))
            if len(observations) > MAX_EVENTS_PER_SOURCE:
                raise ValueError("too_many_events")
    return observations


def _fetch_eonet(categories: tuple[str, ...], transport: Transport, timeout: float,
                 now: datetime) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    requested = [category.strip() for category in categories]
    unknown = [category for category in requested if category not in KNOWN_EONET_CATEGORIES]
    if unknown:
        raise ValueError("invalid_eonet_category:" + ",".join(sorted(unknown)))
    for category in requested:
        body = transport(
            "https://eonet.gsfc.nasa.gov/api/v3/events"
            f"?status=open&limit={EONET_EVENTS_PER_CATEGORY}&category={category}",
            timeout,
        )
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_eonet_response") from exc
        events = data.get("events") if isinstance(data, dict) else None
        if not isinstance(events, list):
            raise ValueError("invalid_eonet_response")
        for entry in events:
            if not isinstance(entry, dict):
                continue
            event_id = _text(entry.get("id"))
            title = _text(entry.get("title"))
            if not event_id or not title or event_id in seen_ids:
                continue
            seen_ids.add(event_id)
            category_ids = [
                _text(item.get("id"))
                for item in entry.get("categories", [])[:8]
                if isinstance(item, dict) and _text(item.get("id"))
            ]
            geometry = entry.get("geometry")
            event_time = lon = lat = None
            if isinstance(geometry, list) and geometry:
                latest = geometry[-1] if isinstance(geometry[-1], dict) else {}
                event_time = _iso_text(latest.get("date"))
                coordinates = latest.get("coordinates")
                if isinstance(coordinates, list) and len(coordinates) >= 2:
                    lon = _coordinate(coordinates[0], 180.0)
                    lat = _coordinate(coordinates[1], 90.0)
            kind = category.lower()
            event = {
                "kind": kind,
                "eonet_id": event_id,
                "title": title,
                "categories": category_ids,
                "event_time": event_time,
                "longitude": lon,
                "latitude": lat,
                "status": "open",
                "source": "nasa_eonet_v3",
            }
            observations.append(_observation(f"EONET:{event_id.upper()}", PROVIDER_EONET, event, now))
            if len(observations) > MAX_EVENTS_PER_SOURCE:
                raise ValueError("too_many_events")
    return observations


def _fetch_open_meteo(cities: tuple[str, ...], transport: Transport, timeout: float,
                      now: datetime,
                      city_registry: dict[str, tuple[float, float]] | None = None
                      ) -> list[dict[str, Any]]:
    registry = city_registry or DEFAULT_CITIES
    observations: list[dict[str, Any]] = []
    for raw_city in cities:
        slug = raw_city.strip().lower()
        if not _CITY_SLUG.fullmatch(slug):
            raise ValueError("invalid_city_slug")
        if slug not in registry:
            raise ValueError("unknown_city:" + slug)
        latitude, longitude = registry[slug]
        body = transport(
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={latitude}&longitude={longitude}"
            "&current=temperature_2m,precipitation,wind_speed_10m&timezone=UTC",
            timeout,
        )
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_open_meteo_response") from exc
        current = data.get("current") if isinstance(data, dict) else None
        if not isinstance(current, dict):
            raise ValueError("invalid_open_meteo_response")
        temperature = _number(current.get("temperature_2m"))
        wind_speed = _number(current.get("wind_speed_10m"))
        precipitation = _number(current.get("precipitation"))
        if temperature is None or not -100.0 <= temperature <= 80.0:
            raise ValueError("invalid_open_meteo_response")
        if wind_speed is not None and not 0.0 <= wind_speed <= 500.0:
            wind_speed = None
        if precipitation is not None and not 0.0 <= precipitation <= 2000.0:
            precipitation = None
        event = {
            "kind": "weather",
            "city": slug,
            "latitude": latitude,
            "longitude": longitude,
            "temperature_c": temperature,
            "wind_speed_kmh": wind_speed,
            "precipitation_mm": precipitation,
            "event_time": _iso_text(current.get("time")),
            "source": "open_meteo_current",
        }
        observations.append(_observation(f"WX:{slug.upper()}", PROVIDER_OPEN_METEO, event, now))
        if len(observations) > MAX_EVENTS_PER_SOURCE:
            raise ValueError("too_many_events")
    return observations


_SOURCE_FETCHERS = {
    PROVIDER_USGS: _fetch_usgs,
    PROVIDER_EONET: _fetch_eonet,
    PROVIDER_OPEN_METEO: _fetch_open_meteo,
}


def collect_world_observations(
    *,
    watchlist: dict[str, tuple[str, ...]] | None = None,
    transport: Transport = _http_get,
    timeout_seconds: float = 8.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch world events from every keyless source; errors are collected,
    never fatal, and no event is ever fabricated."""
    if not 1 <= timeout_seconds <= 30:
        raise ValueError("invalid_http_timeout")
    watchlist = watchlist or DEFAULT_WORLD_WATCHLIST
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


def watch_and_ingest_world(
    store: ResearchStore,
    *,
    allowed_providers: set[str] | frozenset[str] | None = None,
    watchlist: dict[str, tuple[str, ...]] | None = None,
    transport: Transport = _http_get,
    timeout_seconds: float = 8.0,
    max_age_seconds: float = 300.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One world-watch cycle: fetch world events, then run every event
    through the fail-closed ingestion gates into the research store.

    Duplicate events (same event, unchanged data) are counted and skipped,
    not stored twice; an updated event (e.g. revised magnitude) appends as
    a new immutable record.
    """
    current = now or _now_utc()
    collected = collect_world_observations(
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
        "cycle": "world_event_watch",
        "fetched": len(collected["observations"]),
        "ingested": ingested,
        "duplicates_skipped": duplicates,
        "rejected": rejected,
        "source_errors": collected["errors"],
        "collected_at": collected["collected_at"],
        "live_execution": False,
        "broker_order_submitted": False,
    }
