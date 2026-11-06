"""Celestial watch layer: the network observes natural and cosmic activity.

Two families, both keyless:

- ``noaa-swpc`` — real measured space weather from NOAA SWPC public JSON
  products (no account, key or payment): planetary K-index (geomagnetic
  storms), solar-wind plasma, GOES X-ray flux (solar flares) and SWPC
  alerts. These have a physical market channel: geomagnetic storms disturb
  power grids, satellites, GNSS and HF radio.
- ``astro-computed`` — deterministic astronomy computed locally from
  standard low-precision ephemeris math (Meeus Sun/Moon, JPL Keplerian
  elements 1800–2050): lunar phase with tithi/amavasya/poornima, sidereal
  rashi and nakshatra of the Moon, Moon distance/perigee, planetary
  geocentric geometry (conjunction, opposition, close approach to Earth)
  and approximate eclipse windows. No network, no ephemeris download —
  the network computes the sky itself.

Honesty notes baked into every payload:
- Space weather is physical measurement; lunar/planetary items are
  geometry. ``market_channel`` labels each: ``infrastructure`` vs
  ``behavioral_research``. Any market link for the latter is correlation
  research only — causality is never established (see event_market_impact).
- Astronomy is low-precision (~0.3° for the Moon); eclipse flags are
  approximate windows, not exact eclipse predictions; ritual-grade timing
  needs a full ephemeris. Bounds are documented in the payload.

Fail-closed: malformed sources are recorded as errors; nothing is
fabricated. Live trading remains disabled.
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

PROVIDER_NOAA = "noaa-swpc"
PROVIDER_ASTRO = "astro-computed"

DEFAULT_PROVIDERS = frozenset({PROVIDER_NOAA, PROVIDER_ASTRO})

#: Keyless default celestial watchlist. Operators can extend it.
DEFAULT_CELESTIAL_WATCHLIST: dict[str, tuple[str, ...]] = {
    PROVIDER_ASTRO: ("lunar", "planets", "eclipse"),
    PROVIDER_NOAA: ("kp-index", "solar-wind", "xray-flux", "alerts"),
}

NOAA_TOPICS = frozenset({"kp-index", "solar-wind", "xray-flux", "alerts"})
ASTRO_TOPICS = frozenset({"lunar", "planets", "eclipse"})

MAX_RESPONSE_BYTES = 2 * 1_048_576
MAX_TEXT_LENGTH = 240
MAX_ALERT_OBSERVATIONS = 10

RASHI_NAMES = (
    "Mesha", "Vrishabha", "Mithuna", "Karka", "Simha", "Kanya", "Tula",
    "Vrishchika", "Dhanu", "Makara", "Kumbha", "Meena",
)
NAKSHATRA_NAMES = (
    "Ashwini", "Bharani", "Krittika", "Rohini", "Mrigashira", "Ardra",
    "Punarvasu", "Pushya", "Ashlesha", "Magha", "Purva Phalguni",
    "Uttara Phalguni", "Hasta", "Chitra", "Swati", "Vishakha", "Anuradha",
    "Jyeshtha", "Mula", "Purva Ashadha", "Uttara Ashadha", "Shravana",
    "Dhanishta", "Shatabhisha", "Purva Bhadrapada", "Uttara Bhadrapada",
    "Revati",
)

#: JPL approximate Keplerian elements (valid 1800–2050): a[AU], e, I, L,
#: longitude of perihelion, longitude of node — plus per-century rates.
_JPL_ELEMENTS = {
    "Mercury": (0.38709927, 0.20563593, 7.00497902, 252.25032350, 77.45779628, 48.33076593,
                0.00000037, 0.00001906, -0.00594749, 149472.67411175, 0.16047689, -0.12534081),
    "Venus": (0.72333566, 0.00677672, 3.39467605, 181.97909950, 131.60246718, 76.67984255,
              0.00000390, -0.00004107, -0.00078890, 58517.81538729, 0.00268329, -0.27769418),
    "Earth": (1.00000261, 0.01671123, -0.00001531, 100.46457166, 102.93768193, 0.0,
              0.00000562, -0.00004392, -0.01294668, 35999.37244981, 0.32327364, 0.0),
    "Mars": (1.52371034, 0.09339410, 1.84969142, -4.55343205, -23.94362959, 49.55953891,
             0.00001847, 0.00007882, -0.00813131, 19140.30268499, 0.44441088, -0.29257343),
    "Jupiter": (5.20288700, 0.04838624, 1.30439695, 34.39644051, 14.72847983, 100.47390909,
                -0.00011607, -0.00013253, -0.00183714, 3034.74612775, 0.21252668, 0.20469106),
    "Saturn": (9.53667594, 0.05386179, 2.48599187, 49.95424423, 92.59887831, 113.66242448,
               -0.00125060, -0.00050991, 0.00193609, 1222.49362201, -0.41897216, -0.28867794),
    "Uranus": (19.18916464, 0.04725744, 0.77263783, 313.23810451, 170.95427630, 74.01692503,
               -0.00196176, -0.00004397, -0.00242939, 428.48202785, 0.40805281, 0.04240589),
    "Neptune": (30.06992276, 0.00859048, 1.77004347, -55.12002969, 44.96476227, 131.78422574,
                0.00026291, 0.00005105, 0.00035372, 218.45945325, -0.32241464, -0.00508664),
}

_J2000 = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
CONJUNCTION_DEG = 10.0
OPPOSITION_DEG = 170.0
#: A planet is "close to Earth" inside the nearest 15% of its possible
#: distance range [a_planet − a_earth, a_planet + a_earth] — meaningful for
#: both inner and outer planets.
CLOSE_RANGE_FRACTION = 0.15
PERIGEE_KM = 360_000.0
SOLAR_ECLIPSE_ELONG_DEG = 12.0
SOLAR_ECLIPSE_LAT_DEG = 1.6
LUNAR_ECLIPSE_ELONG_DEG = 12.0
LUNAR_ECLIPSE_LAT_DEG = 1.1

Transport = Callable[[str, float], bytes]


def _http_get(url: str, timeout: float) -> bytes:
    request = Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "Osiris-Eye/0.3 celestial-watch",
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
    return re.sub(r"\s+", " ", text)[:MAX_TEXT_LENGTH]


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


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


# ---------------------------------------------------------------------------
# NOAA SWPC measured space weather
# ---------------------------------------------------------------------------

def _g_scale(kp: float) -> str | None:
    if kp >= 9.0:
        return "G5"
    if kp >= 8.0:
        return "G4"
    if kp >= 7.0:
        return "G3"
    if kp >= 6.0:
        return "G2"
    if kp >= 5.0:
        return "G1"
    return None


def _flare_class(flux: float) -> tuple[str, str]:
    """(class string like 'M2.3', title token like 'M-class')."""
    if flux >= 1e-4:
        return f"X{flux / 1e-4:.1f}", "X-class"
    if flux >= 1e-5:
        return f"M{flux / 1e-5:.1f}", "M-class"
    if flux >= 1e-6:
        return f"C{flux / 1e-6:.1f}", "C-class"
    return "quiet", "below C-class"


def _fetch_kp(transport: Transport, timeout: float) -> dict[str, Any]:
    body = transport("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json", timeout)
    try:
        rows = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_noaa_kp_response") from exc
    if not isinstance(rows, list) or len(rows) < 2 or not isinstance(rows[-1], list) or len(rows[-1]) < 4:
        raise ValueError("invalid_noaa_kp_response")
    latest = rows[-1]
    kp = _number(latest[1])
    event_time = _iso_text(latest[0])
    if kp is None or not 0.0 <= kp <= 9.0 or event_time is None:
        raise ValueError("invalid_noaa_kp_response")
    stations = _number(latest[3])
    g_scale = _g_scale(kp)
    title = (f"Geomagnetic storm {g_scale} (Kp {kp})" if g_scale
             else f"Geomagnetic field quiet (Kp {kp})")
    return {
        "kind": "geomagnetic",
        "kp": kp,
        "g_scale": g_scale,
        "station_count": int(stations) if stations is not None else None,
        "event_time": event_time,
        "title": title,
        "market_channel": "infrastructure",
        "source": "noaa_swpc_k_index",
    }


def _fetch_solar_wind(transport: Transport, timeout: float) -> dict[str, Any]:
    body = transport("https://services.swpc.noaa.gov/products/solar-wind/plasma-1-day.json", timeout)
    try:
        rows = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_noaa_plasma_response") from exc
    if not isinstance(rows, list) or len(rows) < 2 or not isinstance(rows[-1], list) or len(rows[-1]) < 3:
        raise ValueError("invalid_noaa_plasma_response")
    latest = rows[-1]
    speed = _number(latest[2])
    density = _number(latest[1])
    event_time = _iso_text(latest[0])
    if speed is None or not 100.0 <= speed <= 2500.0 or event_time is None:
        raise ValueError("invalid_noaa_plasma_response")
    if density is not None and not 0.0 <= density <= 500.0:
        density = None
    high_speed = speed >= 600.0
    title = f"Solar wind {speed:.0f} km/s" + (" high-speed stream" if high_speed else "")
    return {
        "kind": "solar_wind",
        "speed_km_s": round(speed, 1),
        "density_p_cm3": round(density, 2) if density is not None else None,
        "high_speed_stream": high_speed,
        "event_time": event_time,
        "title": title,
        "market_channel": "infrastructure",
        "source": "noaa_swpc_solar_wind",
    }


def _fetch_xray(transport: Transport, timeout: float) -> dict[str, Any]:
    body = transport("https://services.swpc.noaa.gov/json/goes/primary/xrays-1-day.json", timeout)
    try:
        entries = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_noaa_xray_response") from exc
    if not isinstance(entries, list) or not entries:
        raise ValueError("invalid_noaa_xray_response")
    flux = event_time = None
    for entry in reversed(entries[-200:]):
        if not isinstance(entry, dict):
            continue
        flux = _number(entry.get("flux"))
        event_time = _iso_text(entry.get("time_tag"))
        if flux is not None and flux >= 0 and event_time is not None:
            break
        flux = event_time = None
    if flux is None or event_time is None:
        raise ValueError("invalid_noaa_xray_response")
    class_string, class_token = _flare_class(flux)
    title = f"Solar X-ray flux {class_token} ({class_string})"
    return {
        "kind": "xray_flare",
        "flux_wm2": flux,
        "flare_class": class_string,
        "event_time": event_time,
        "title": title,
        "market_channel": "infrastructure",
        "source": "noaa_swpc_goes_xray",
    }


def _fetch_alerts(transport: Transport, timeout: float) -> list[dict[str, Any]]:
    body = transport("https://services.swpc.noaa.gov/products/alerts.json", timeout)
    try:
        entries = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_noaa_alerts_response") from exc
    if not isinstance(entries, list):
        raise ValueError("invalid_noaa_alerts_response")
    events = []
    for entry in entries[:MAX_ALERT_OBSERVATIONS]:
        if not isinstance(entry, dict):
            continue
        message = _text(entry.get("message"))
        issue_time = _iso_text(entry.get("issue_datetime"))
        product_id = _text(entry.get("product_id"))
        if not message or issue_time is None:
            continue
        events.append({
            "kind": "swpc_alert",
            "product_id": product_id or None,
            "event_time": issue_time,
            "title": message[:140],
            "message_excerpt": message,
            "market_channel": "infrastructure",
            "source": "noaa_swpc_alerts",
        })
    return events


def _fetch_noaa(topics: tuple[str, ...], transport: Transport, timeout: float,
                now: datetime) -> list[dict[str, Any]]:
    unknown = [topic for topic in topics if topic not in NOAA_TOPICS]
    if unknown:
        raise ValueError("invalid_noaa_topic:" + ",".join(sorted(unknown)))
    observations: list[dict[str, Any]] = []
    for topic in topics:
        if topic == "kp-index":
            event = _fetch_kp(transport, timeout)
            observations.append(_observation("SWPC:KP", PROVIDER_NOAA, event, now))
        elif topic == "solar-wind":
            event = _fetch_solar_wind(transport, timeout)
            observations.append(_observation("SWPC:SOLARWIND", PROVIDER_NOAA, event, now))
        elif topic == "xray-flux":
            event = _fetch_xray(transport, timeout)
            observations.append(_observation("SWPC:XRAY", PROVIDER_NOAA, event, now))
        elif topic == "alerts":
            for event in _fetch_alerts(transport, timeout):
                observations.append(_observation("SWPC:ALERT", PROVIDER_NOAA, event, now))
    return observations


# ---------------------------------------------------------------------------
# Computed astronomy (deterministic, no network)
# ---------------------------------------------------------------------------

def _days_since_j2000(now: datetime) -> float:
    return (now.astimezone(timezone.utc) - _J2000).total_seconds() / 86400.0


def _sun_longitude(d: float) -> float:
    mean_longitude = 280.460 + 0.9856474 * d
    g = math.radians((357.528 + 0.9856003 * d) % 360.0)
    return (mean_longitude + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) % 360.0


def _moon_position(d: float) -> tuple[float, float, float]:
    """(ecliptic longitude deg, ecliptic latitude deg, distance km) — Meeus
    low precision (~0.3° in longitude)."""
    mean_longitude = (218.316 + 13.176396 * d) % 360.0
    mean_anomaly = math.radians((134.963 + 13.064993 * d) % 360.0)
    argument_latitude = math.radians((93.272 + 13.229350 * d) % 360.0)
    longitude = (mean_longitude + 6.289 * math.sin(mean_anomaly)) % 360.0
    latitude = 5.128 * math.sin(argument_latitude)
    distance_km = 385_001.0 - 20_905.0 * math.cos(mean_anomaly)
    return longitude, latitude, distance_km


def _ayanamsa_lahiri(d: float) -> float:
    """Linear Lahiri ayanamsa approximation (deg)."""
    year = 2000.0 + d / 365.25
    return 23.853 + (year - 2000.0) * 0.01397


def _helio_xyz(name: str, d: float) -> tuple[float, float, float]:
    base = _JPL_ELEMENTS[name]
    centuries = d / 36525.0
    a, eccentricity, inclination, mean_longitude, lon_perihelion, lon_node = (
        base[i] + base[i + 6] * centuries for i in range(6)
    )
    arg_perihelion = math.radians(lon_perihelion - lon_node)
    mean_anomaly = math.radians((mean_longitude - lon_perihelion) % 360.0)
    eccentric_anomaly = mean_anomaly
    for _ in range(8):
        eccentric_anomaly -= (
            (eccentric_anomaly - eccentricity * math.sin(eccentric_anomaly) - mean_anomaly)
            / (1.0 - eccentricity * math.cos(eccentric_anomaly))
        )
    true_anomaly = 2.0 * math.atan2(
        math.sqrt(1.0 + eccentricity) * math.sin(eccentric_anomaly / 2.0),
        math.sqrt(1.0 - eccentricity) * math.cos(eccentric_anomaly / 2.0),
    )
    radius = a * (1.0 - eccentricity * math.cos(eccentric_anomaly))
    argument_latitude = arg_perihelion + true_anomaly
    node = math.radians(lon_node)
    inc = math.radians(inclination)
    return (
        radius * (math.cos(node) * math.cos(argument_latitude)
                  - math.sin(node) * math.sin(argument_latitude) * math.cos(inc)),
        radius * (math.sin(node) * math.cos(argument_latitude)
                  + math.cos(node) * math.sin(argument_latitude) * math.cos(inc)),
        radius * (math.sin(argument_latitude) * math.sin(inc)),
    )


def _lunar_event(now: datetime) -> dict[str, Any]:
    d = _days_since_j2000(now)
    sun_lon = _sun_longitude(d)
    moon_lon, moon_lat, moon_km = _moon_position(d)
    elongation = (moon_lon - sun_lon) % 360.0
    illumination = (1.0 - math.cos(math.radians(elongation))) / 2.0
    tithi = int(elongation // 12.0) + 1
    is_amavasya = tithi == 30
    is_poornima = tithi == 15
    paksha = "shukla" if elongation < 180.0 else "krishna"
    sidereal = (moon_lon - _ayanamsa_lahiri(d)) % 360.0
    rashi_index = min(int(sidereal // 30.0), 11)
    nakshatra_span = 360.0 / 27.0
    nakshatra_index = min(int(sidereal // nakshatra_span), 26)
    pada = min(int((sidereal % nakshatra_span) // (nakshatra_span / 4.0)) + 1, 4)
    perigee_near = moon_km < PERIGEE_KM
    rashi = RASHI_NAMES[rashi_index]
    nakshatra = NAKSHATRA_NAMES[nakshatra_index]
    if is_amavasya:
        title = (f"Amavasya (new moon) — Moon in {rashi} rashi, "
                 f"{nakshatra} nakshatra pada {pada} (tithi {tithi})")
    elif is_poornima:
        title = (f"Poornima (full moon) — Moon in {rashi} rashi, "
                 f"{nakshatra} nakshatra pada {pada} (tithi {tithi})")
    else:
        phase = "Waxing (shukla paksha)" if paksha == "shukla" else "Waning (krishna paksha)"
        title = (f"{phase} — Moon in {rashi} rashi, {nakshatra} nakshatra "
                 f"pada {pada} (tithi {tithi})")
    return {
        "kind": "lunar",
        "elongation_deg": round(elongation, 2),
        "illumination": round(illumination, 4),
        "tithi": tithi,
        "paksha": paksha,
        "is_amavasya": is_amavasya,
        "is_poornima": is_poornima,
        "moon_distance_km": int(round(moon_km)),
        "perigee_near": perigee_near,
        "sidereal_longitude_deg": round(sidereal, 2),
        "rashi": {"index": rashi_index, "name": rashi},
        "nakshatra": {"index": nakshatra_index, "name": nakshatra, "pada": pada},
        "event_time": now.isoformat(),
        "title": _text(title),
        "market_channel": "behavioral_research",
        "source": "astro_computed_meeus_low_precision",
        "precision_note": "moon longitude ~0.3 deg; ritual-grade timing needs full ephemeris",
    }


def _planets_event(now: datetime) -> dict[str, Any]:
    d = _days_since_j2000(now)
    earth = _helio_xyz("Earth", d)
    earth_a = _JPL_ELEMENTS["Earth"][0]
    sun_lon = _sun_longitude(d)
    planets = []
    flagged: list[tuple[int, str]] = []
    for name in ("Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune"):
        px, py, pz = _helio_xyz(name, d)
        dx, dy, dz = px - earth[0], py - earth[1], pz - earth[2]
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        geocentric_lon = math.degrees(math.atan2(dy, dx)) % 360.0
        elongation = (geocentric_lon - sun_lon) % 360.0
        if elongation > 180.0:
            elongation = 360.0 - elongation
        planet_a = _JPL_ELEMENTS[name][0]
        min_distance = abs(planet_a - earth_a)
        max_distance = planet_a + earth_a
        conjunction = elongation < CONJUNCTION_DEG
        opposition = elongation > OPPOSITION_DEG
        close_approach = distance < min_distance + CLOSE_RANGE_FRACTION * (max_distance - min_distance)
        planets.append({
            "name": name,
            "geocentric_longitude_deg": round(geocentric_lon, 2),
            "distance_au": round(distance, 3),
            "elongation_deg": round(elongation, 1),
            "conjunction_with_sun": conjunction,
            "opposition": opposition,
            "close_approach_to_earth": close_approach,
        })
        if opposition:
            flagged.append((0, f"{name}: opposition window (elongation {elongation:.0f} deg)"))
        if close_approach:
            flagged.append((1, f"{name}: close approach to Earth ({distance:.2f} AU)"))
        elif conjunction:
            flagged.append((2, f"{name}: conjunction with Sun (elongation {elongation:.0f} deg)"))
    flagged.sort(key=lambda item: item[0])
    events = [text for _, text in flagged[:6]]
    title = "; ".join(events[:2]) if events else "Planetary geometry nominal"
    return {
        "kind": "planetary",
        "planets": planets,
        "events": events,
        "event_time": now.isoformat(),
        "title": _text(title),
        "market_channel": "behavioral_research",
        "source": "astro_computed_jpl_keplerian_1800_2050",
        "precision_note": "approximate elements; flags are windows, not exact ephemeris moments",
    }


def _eclipse_event(now: datetime) -> dict[str, Any]:
    d = _days_since_j2000(now)
    sun_lon = _sun_longitude(d)
    moon_lon, moon_lat, _ = _moon_position(d)
    elongation = (moon_lon - sun_lon) % 360.0
    near_new = min(elongation, 360.0 - elongation)
    near_full = abs(elongation - 180.0)
    abs_lat = abs(moon_lat)
    solar_window = near_new < SOLAR_ECLIPSE_ELONG_DEG and abs_lat < SOLAR_ECLIPSE_LAT_DEG
    lunar_window = near_full < LUNAR_ECLIPSE_ELONG_DEG and abs_lat < LUNAR_ECLIPSE_LAT_DEG
    if solar_window:
        title = "Solar eclipse window near new moon (approximate)"
    elif lunar_window:
        title = "Lunar eclipse window near full moon (approximate)"
    else:
        title = "No eclipse window"
    return {
        "kind": "eclipse",
        "solar_window": solar_window,
        "lunar_window": lunar_window,
        "elongation_deg": round(elongation, 2),
        "moon_latitude_deg": round(moon_lat, 3),
        "approximate_window": True,
        "event_time": now.isoformat(),
        "title": title,
        "market_channel": "behavioral_research",
        "source": "astro_computed_eclipse_limits",
        "precision_note": "eclipse season window from elongation + latitude limits; "
                          "not an exact eclipse prediction",
    }


def _fetch_astro(topics: tuple[str, ...], transport: Transport, timeout: float,
                 now: datetime) -> list[dict[str, Any]]:
    unknown = [topic for topic in topics if topic not in ASTRO_TOPICS]
    if unknown:
        raise ValueError("invalid_astro_topic:" + ",".join(sorted(unknown)))
    observations: list[dict[str, Any]] = []
    for topic in topics:
        if topic == "lunar":
            observations.append(_observation("ASTRO:LUNAR", PROVIDER_ASTRO, _lunar_event(now), now))
        elif topic == "planets":
            observations.append(_observation("ASTRO:PLANETS", PROVIDER_ASTRO, _planets_event(now), now))
        elif topic == "eclipse":
            observations.append(_observation("ASTRO:ECLIPSE", PROVIDER_ASTRO, _eclipse_event(now), now))
    return observations


_SOURCE_FETCHERS = {
    PROVIDER_ASTRO: _fetch_astro,
    PROVIDER_NOAA: _fetch_noaa,
}


def collect_celestial_observations(
    *,
    watchlist: dict[str, tuple[str, ...]] | None = None,
    transport: Transport = _http_get,
    timeout_seconds: float = 8.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch measured space weather and compute the sky; errors are
    collected, never fatal, and nothing is ever fabricated."""
    if not 1 <= timeout_seconds <= 30:
        raise ValueError("invalid_http_timeout")
    watchlist = watchlist or DEFAULT_CELESTIAL_WATCHLIST
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


def watch_and_ingest_celestial(
    store: ResearchStore,
    *,
    allowed_providers: set[str] | frozenset[str] | None = None,
    watchlist: dict[str, tuple[str, ...]] | None = None,
    transport: Transport = _http_get,
    timeout_seconds: float = 8.0,
    max_age_seconds: float = 300.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One celestial-watch cycle: measure space weather + compute the sky,
    then run every observation through the fail-closed ingestion gates.

    Unchanged observations are counted as duplicates and skipped; changed
    ones append as new immutable records.
    """
    current = now or _now_utc()
    collected = collect_celestial_observations(
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
        "cycle": "celestial_watch",
        "fetched": len(collected["observations"]),
        "ingested": ingested,
        "duplicates_skipped": duplicates,
        "rejected": rejected,
        "source_errors": collected["errors"],
        "collected_at": collected["collected_at"],
        "live_execution": False,
        "broker_order_submitted": False,
    }
