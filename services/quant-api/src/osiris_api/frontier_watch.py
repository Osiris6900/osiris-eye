"""Frontier watch layer: rivers, radiation, satellites, volcanoes.

Extends the network's senses beyond markets, weather, sky and sea with
four more keyless public sources (no API key, no signup, no payment),
each normalized into the signed ``osiris.observation.v1`` contract for
the authorized ingestion pipeline:

- USGS Water Services — live river discharge and gage height at major
  US gauges (direct flood measurement, not a report about a flood).
- Safecast — citizen Geiger-counter measurements near monitored sites
  (the fastest public signal of a radiation incident).
- CelesTrak — current TLE orbital elements for the crewed-stations
  group (who is overhead, on which orbit, as of which epoch).
- Smithsonian/USGS GVP — Weekly Volcanic Activity Report items
  (eruptions and unrest with aviation-relevant detail).

Fail-closed: unreachable or malformed sources are collected as errors,
never fatal; no reading is ever invented. ``observed_at`` is collection
time; the measurement's own time stays in the payload. Live trading
remains disabled.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .data_store import ResearchStore
from .p2p_ingestion import IngestionRejection, ingest_p2p_observation

PROVIDER_USGS_WATER = "usgs-water"
PROVIDER_SAFECAST = "safecast"
PROVIDER_CELESTRAK = "celestrak"
PROVIDER_GVP = "smithsonian-gvp"

DEFAULT_PROVIDERS = frozenset({
    PROVIDER_USGS_WATER, PROVIDER_SAFECAST, PROVIDER_CELESTRAK, PROVIDER_GVP,
})

#: Keyless default frontier watchlist.
DEFAULT_FRONTIER_WATCHLIST: dict[str, tuple[str, ...]] = {
    PROVIDER_USGS_WATER: ("01646500", "11447650", "07374000"),
    PROVIDER_SAFECAST: ("fukushima", "tokyo", "chernobyl", "new-york"),
    PROVIDER_CELESTRAK: ("stations",),
    PROVIDER_GVP: ("weekly",),
}

#: USGS river gauges worth watching: site id → short label.
DEFAULT_GAUGE_LABELS: dict[str, str] = {
    "01646500": "Potomac River near Washington DC",
    "11447650": "Sacramento River at Freeport, California",
    "07374000": "Mississippi River at Baton Rouge, Louisiana",
}

#: Radiation monitor points: label → (latitude, longitude, radius km).
DEFAULT_RADIATION_SITES: dict[str, tuple[float, float, int]] = {
    "fukushima": (37.42, 141.03, 50),
    "tokyo": (35.68, 139.69, 50),
    "chernobyl": (51.39, 30.10, 50),
    "new-york": (40.71, -74.00, 50),
}

#: CelesTrak groups this layer knows (keyless TLE files).
KNOWN_CELESTRAK_GROUPS = frozenset({
    "stations", "visual", "active", "science", "noaa", "goes",
})

KNOWN_GVP_FEEDS = frozenset({"weekly"})

MAX_EVENTS_PER_SOURCE = 100
MAX_RESPONSE_BYTES = 2 * 1_048_576
MAX_TEXT_LENGTH = 240
MAX_TLES = 25
MAX_GVP_ITEMS = 40

_GAUGE_ID = re.compile(r"[0-9]{8,16}")
_SITE_SLUG = re.compile(r"[a-z0-9-]{2,40}")

Transport = Callable[[str, float], bytes]


def _http_get(url: str, timeout: float) -> bytes:
    request = Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "Osiris-Eye/0.3 frontier-watch",
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


def _fetch_usgs_water(sites: tuple[str, ...], transport: Transport, timeout: float,
                      now: datetime,
                      gauge_labels: dict[str, str] | None = None) -> list[dict[str, Any]]:
    labels = gauge_labels or DEFAULT_GAUGE_LABELS
    observations: list[dict[str, Any]] = []
    for raw_site in sites:
        site = raw_site.strip()
        if not _GAUGE_ID.fullmatch(site):
            raise ValueError("invalid_usgs_water_site")
        body = transport(
            "https://waterservices.usgs.gov/nwis/iv/"
            f"?format=json&sites={site}&parameterCd=00060,00065&siteStatus=active",
            timeout,
        )
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_usgs_water_response") from exc
        series = data.get("value", {}).get("timeSeries") if isinstance(data, dict) else None
        if not isinstance(series, list) or not series:
            raise ValueError("invalid_usgs_water_response")
        site_name = discharge_cfs = gage_height_ft = event_time = None
        for entry in series:
            if not isinstance(entry, dict):
                continue
            info = entry.get("sourceInfo") if isinstance(entry.get("sourceInfo"), dict) else {}
            if site_name is None:
                site_name = _text(info.get("siteName")) or None
            variable = entry.get("variable") if isinstance(entry.get("variable"), dict) else {}
            codes = variable.get("variableCode") if isinstance(variable.get("variableCode"), list) else []
            code = _text(codes[0].get("value")) if codes and isinstance(codes[0], dict) else ""
            values = entry.get("values") if isinstance(entry.get("values"), list) else []
            points = values[0].get("value") if values and isinstance(values[0], dict) else []
            if not points or not isinstance(points[0], dict):
                continue
            reading = points[0]
            if event_time is None:
                event_time = _iso_text(reading.get("dateTime"))
            if code == "00060":
                discharge_cfs = _bounded(reading.get("value"), 0.0, 10_000_000.0)
            elif code == "00065":
                gage_height_ft = _bounded(reading.get("value"), -10.0, 200.0)
        if discharge_cfs is None and gage_height_ft is None:
            raise ValueError("invalid_usgs_water_response")
        event = {
            "kind": "river_gauge",
            "place": labels.get(site, site_name or f"USGS gauge {site}"),
            "usgs_site": site,
            "site_name": site_name,
            "event_time": event_time,
            "discharge_cfs": discharge_cfs,
            "gage_height_ft": gage_height_ft,
            "source": "usgs_water_services_iv",
        }
        observations.append(_observation(f"RIV:{site}", PROVIDER_USGS_WATER, event, now))
        if len(observations) > MAX_EVENTS_PER_SOURCE:
            raise ValueError("too_many_events")
    return observations


def _fetch_safecast(sites: tuple[str, ...], transport: Transport, timeout: float,
                    now: datetime,
                    radiation_sites: dict[str, tuple[float, float, int]] | None = None
                    ) -> list[dict[str, Any]]:
    registry = radiation_sites or DEFAULT_RADIATION_SITES
    observations: list[dict[str, Any]] = []
    for raw_site in sites:
        slug = raw_site.strip().lower()
        if not _SITE_SLUG.fullmatch(slug):
            raise ValueError("invalid_radiation_site")
        if slug not in registry:
            raise ValueError("unknown_radiation_site:" + slug)
        latitude, longitude, radius = registry[slug]
        body = transport(
            "https://api.safecast.org/measurements.json"
            f"?distance={radius}&latitude={latitude}&longitude={longitude}",
            timeout,
        )
        try:
            rows = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_safecast_response") from exc
        if not isinstance(rows, list):
            raise ValueError("invalid_safecast_response")
        newest: dict[str, Any] | None = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            captured = _iso_text(row.get("captured_at"))
            value = _bounded(row.get("value"), 0.0, 1_000_000.0)
            if captured is None or value is None:
                continue
            if newest is None or captured > newest["captured"]:
                newest = {"captured": captured, "row": row, "value": value}
        if newest is None:
            event = {
                "kind": "radiation",
                "place": f"{slug} radiation monitor",
                "site": slug,
                "latitude": latitude,
                "longitude": longitude,
                "event_time": None,
                "value": None,
                "unit": None,
                "status": "no_recent_measurements",
                "source": "safecast_api",
            }
        else:
            row = newest["row"]
            event = {
                "kind": "radiation",
                "place": f"{slug} radiation monitor",
                "site": slug,
                "latitude": _bounded(row.get("latitude"), -90.0, 90.0),
                "longitude": _bounded(row.get("longitude"), -180.0, 180.0),
                "event_time": newest["captured"],
                "value": newest["value"],
                "unit": _text(row.get("unit")) or None,
                "status": "measured",
                "source": "safecast_api",
            }
        observations.append(_observation(f"RAD:{slug.upper()}", PROVIDER_SAFECAST, event, now))
        if len(observations) > MAX_EVENTS_PER_SOURCE:
            raise ValueError("too_many_events")
    return observations


def _fetch_celestrak(groups: tuple[str, ...], transport: Transport, timeout: float,
                     now: datetime) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_group in groups:
        group = raw_group.strip().lower()
        if group not in KNOWN_CELESTRAK_GROUPS:
            raise ValueError("invalid_celestrak_group:" + group)
        body = transport(
            f"https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle",
            timeout,
        )
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("invalid_celestrak_response") from exc
        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        index = 0
        while index + 2 < len(lines):
            name, line1, line2 = lines[index], lines[index + 1], lines[index + 2]
            index += 3
            if not (line1.startswith("1 ") and line2.startswith("2 ")):
                break
            norad_id = _text(line1[2:7])
            if not norad_id or norad_id in seen_ids:
                continue
            seen_ids.add(norad_id)
            mean_motion = _bounded(line2[52:63], 0.0, 20.0)
            event = {
                "kind": "satellite_tle",
                "title": _text(name),
                "norad_id": norad_id,
                "group": group,
                "tle_line1": _text(line1),
                "tle_line2": _text(line2),
                "mean_motion_rev_day": mean_motion,
                "event_time": None,  # TLE epoch stays in line1; no separate clock here
                "source": "celestrak_gp_tle",
            }
            observations.append(_observation(f"SAT:{norad_id}", PROVIDER_CELESTRAK, event, now))
            if len(observations) >= MAX_TLES or len(observations) > MAX_EVENTS_PER_SOURCE:
                break
    return observations


def _fetch_gvp(feeds: tuple[str, ...], transport: Transport, timeout: float,
               now: datetime) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for raw_feed in feeds:
        feed = raw_feed.strip().lower()
        if feed not in KNOWN_GVP_FEEDS:
            raise ValueError("invalid_gvp_feed:" + feed)
        body = transport("https://volcano.si.edu/news/WeeklyVolcanoRSS.xml", timeout)
        try:
            root = ET.fromstring(body.decode("utf-8", errors="replace"))
        except ET.ParseError as exc:
            raise ValueError("invalid_gvp_response") from exc
        items = root.findall(".//item")[:MAX_GVP_ITEMS]
        for item in items:
            title = _text(item.findtext("title"))
            link = _text(item.findtext("link"))
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            pub_date = _text(item.findtext("pubDate"))
            event_time = None
            if pub_date:
                try:
                    event_time = parsedate_to_datetime(pub_date).astimezone(timezone.utc).isoformat()
                except (TypeError, ValueError, OverflowError):
                    event_time = None
            event = {
                "kind": "volcano_report",
                "title": title,
                "link": link,
                "event_time": event_time,
                "source": "smithsonian_gvp_weekly_rss",
            }
            digest = hashlib.sha256(title.encode("utf-8")).hexdigest()[:16].upper()
            observations.append(_observation(f"VOL:{digest}", PROVIDER_GVP, event, now))
            if len(observations) > MAX_EVENTS_PER_SOURCE:
                raise ValueError("too_many_events")
    return observations


_SOURCE_FETCHERS = {
    PROVIDER_USGS_WATER: _fetch_usgs_water,
    PROVIDER_SAFECAST: _fetch_safecast,
    PROVIDER_CELESTRAK: _fetch_celestrak,
    PROVIDER_GVP: _fetch_gvp,
}


def collect_frontier_observations(
    *,
    watchlist: dict[str, tuple[str, ...]] | None = None,
    transport: Transport = _http_get,
    timeout_seconds: float = 8.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch frontier readings from every keyless source; errors are
    collected, never fatal, and no reading is ever fabricated."""
    if not 1 <= timeout_seconds <= 30:
        raise ValueError("invalid_http_timeout")
    watchlist = watchlist or DEFAULT_FRONTIER_WATCHLIST
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


def watch_and_ingest_frontier(
    store: ResearchStore,
    *,
    allowed_providers: set[str] | frozenset[str] | None = None,
    watchlist: dict[str, tuple[str, ...]] | None = None,
    transport: Transport = _http_get,
    timeout_seconds: float = 8.0,
    max_age_seconds: float = 300.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One frontier-watch cycle: fetch readings, then run every reading
    through the fail-closed ingestion gates into the research store.

    Duplicate readings (same reading, unchanged data) are counted and
    skipped, not stored twice; an updated reading appends as a new
    immutable record.
    """
    current = now or _now_utc()
    collected = collect_frontier_observations(
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
        "cycle": "frontier_watch",
        "fetched": len(collected["observations"]),
        "ingested": ingested,
        "duplicates_skipped": duplicates,
        "rejected": rejected,
        "source_errors": collected["errors"],
        "collected_at": collected["collected_at"],
        "live_execution": False,
        "broker_order_submitted": False,
    }
