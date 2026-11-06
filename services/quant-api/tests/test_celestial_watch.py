"""Tests for the celestial watch layer (measured space weather + computed sky)."""
import hashlib
import json
from datetime import datetime, timezone

import pytest

from osiris_api.celestial_watch import (
    DEFAULT_CELESTIAL_WATCHLIST,
    PROVIDER_ASTRO,
    PROVIDER_NOAA,
    collect_celestial_observations,
    watch_and_ingest_celestial,
)
from osiris_api.data_store import ResearchStore
from osiris_api.event_market_impact import match_event_exposure
from osiris_api.p2p_ingestion import ENABLE_ENV

NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)
AMAVASYA = datetime(2000, 1, 6, 18, 14, 0, tzinfo=timezone.utc)   # exact new moon
POORNIMA = datetime(2000, 1, 20, 20, 0, 0, tzinfo=timezone.utc)   # inside tithi 15
SOLAR_ECLIPSE = datetime(1999, 8, 11, 11, 4, 0, tzinfo=timezone.utc)  # total eclipse
LUNAR_ECLIPSE = datetime(2000, 1, 21, 4, 40, 0, tzinfo=timezone.utc)  # total eclipse
MARS_OPPOSITION = datetime(2003, 8, 27, 10, 0, 0, tzinfo=timezone.utc)  # great opposition
PLANETS_QUIET = datetime(2025, 7, 10, 12, 0, 0, tzinfo=timezone.utc)   # no flags

KP_JSON = json.dumps([
    ["time_tag", "Kp", "a_running", "station_count"],
    ["2026-11-06 10:30:00.000", "2.00", "5", "8"],
    ["2026-11-06 11:30:00.000", "6.67", "30", "8"],
]).encode()

KP_QUIET_JSON = json.dumps([
    ["time_tag", "Kp", "a_running", "station_count"],
    ["2026-11-06 11:30:00.000", "2.00", "5", "8"],
]).encode()

PLASMA_JSON = json.dumps([
    ["time_tag", "density", "speed", "temperature"],
    ["2026-11-06 11:58:00.000", "4.21", "612.3", "152340"],
]).encode()

XRAY_JSON = json.dumps([
    {"time_tag": "2026-11-06T11:50:00Z", "satellite": 16, "flux": 1.2e-07},
    {"time_tag": "2026-11-06T11:59:00Z", "satellite": 16, "flux": 2.3e-05},
]).encode()

ALERTS_JSON = json.dumps([
    {"product_id": "K05W", "issue_datetime": "2026-11-06 11:05:00.000",
     "message": "WARNING: Geomagnetic K-index of 5 expected\nValid until 1200 UTC"},
    {"product_id": "WATA", "issue_datetime": "2026-11-06 10:12:00.000",
     "message": "WATCH: Geomagnetic storm category G2 predicted"},
]).encode()


def fake_transport(url: str, timeout: float) -> bytes:
    if "noaa-planetary-k-index" in url:
        return KP_JSON
    if "plasma-1-day" in url:
        return PLASMA_JSON
    if "xrays-1-day" in url:
        return XRAY_JSON
    if "alerts.json" in url:
        return ALERTS_JSON
    raise RuntimeError("watch_source_unavailable")


SMALL_WATCHLIST = {
    PROVIDER_ASTRO: ("lunar", "planets", "eclipse"),
    PROVIDER_NOAA: ("kp-index", "alerts"),
}


@pytest.fixture
def store(tmp_path):
    return ResearchStore(str(tmp_path / "celestial.sqlite3"))


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")


def _payloads(result, instrument):
    return [json.loads(obs["payload"]) for obs in result["observations"]
            if obs["instrument"] == instrument]


# --- collection & normalization -------------------------------------------

def test_collect_returns_all_sources():
    result = collect_celestial_observations(
        watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW,
    )
    assert result["errors"] == []
    instruments = [obs["instrument"] for obs in result["observations"]]
    # providers alphabetical: astro-computed (topics in tuple order), then noaa
    assert instruments == [
        "ASTRO:LUNAR", "ASTRO:PLANETS", "ASTRO:ECLIPSE",
        "SWPC:KP", "SWPC:ALERT", "SWPC:ALERT",
    ]
    for observation in result["observations"]:
        assert observation["schema"] == "osiris.observation.v1"
        assert observation["observed_at"] == NOW.isoformat()
        assert observation["payload_sha256"] == hashlib.sha256(
            observation["payload"].encode("utf-8")
        ).hexdigest()


def test_default_watchlist_only_known_sources():
    assert set(DEFAULT_CELESTIAL_WATCHLIST) == {PROVIDER_ASTRO, PROVIDER_NOAA}


def test_kp_storm_normalized():
    result = collect_celestial_observations(
        watchlist={PROVIDER_NOAA: ("kp-index",)}, transport=fake_transport, now=NOW,
    )
    payload = _payloads(result, "SWPC:KP")[0]
    assert payload["kind"] == "geomagnetic"
    assert payload["kp"] == 6.67
    assert payload["g_scale"] == "G2"
    assert payload["station_count"] == 8
    assert payload["title"] == "Geomagnetic storm G2 (Kp 6.67)"
    assert payload["market_channel"] == "infrastructure"


def test_kp_quiet_title():
    def quiet_transport(url: str, timeout: float) -> bytes:
        return KP_QUIET_JSON
    result = collect_celestial_observations(
        watchlist={PROVIDER_NOAA: ("kp-index",)}, transport=quiet_transport, now=NOW,
    )
    payload = _payloads(result, "SWPC:KP")[0]
    assert payload["g_scale"] is None
    assert "quiet" in payload["title"]


def test_solar_wind_high_speed_stream():
    result = collect_celestial_observations(
        watchlist={PROVIDER_NOAA: ("solar-wind",)}, transport=fake_transport, now=NOW,
    )
    payload = _payloads(result, "SWPC:SOLARWIND")[0]
    assert payload["speed_km_s"] == 612.3
    assert payload["high_speed_stream"] is True
    assert "high-speed stream" in payload["title"]


def test_xray_m_class_flare():
    result = collect_celestial_observations(
        watchlist={PROVIDER_NOAA: ("xray-flux",)}, transport=fake_transport, now=NOW,
    )
    payload = _payloads(result, "SWPC:XRAY")[0]
    assert payload["flare_class"] == "M2.3"
    assert "M-class" in payload["title"]


def test_alerts_normalized():
    result = collect_celestial_observations(
        watchlist={PROVIDER_NOAA: ("alerts",)}, transport=fake_transport, now=NOW,
    )
    payloads = _payloads(result, "SWPC:ALERT")
    assert len(payloads) == 2
    assert payloads[0]["product_id"] == "K05W"
    assert "Geomagnetic K-index of 5" in payloads[0]["title"]
    assert "\n" not in payloads[0]["title"]  # whitespace flattened


def test_malformed_noaa_reported():
    def garbage(url: str, timeout: float) -> bytes:
        return b"not json"
    result = collect_celestial_observations(
        watchlist={PROVIDER_NOAA: ("kp-index",)}, transport=garbage, now=NOW,
    )
    assert result["observations"] == []
    assert "invalid_noaa_kp_response" in result["errors"][0]["error"]


def test_invalid_topics_rejected():
    result = collect_celestial_observations(
        watchlist={PROVIDER_NOAA: ("aurora-cam",)}, transport=fake_transport, now=NOW,
    )
    assert result["errors"][0]["error"].startswith("invalid_noaa_topic")
    result = collect_celestial_observations(
        watchlist={PROVIDER_ASTRO: ("horoscope",)}, transport=fake_transport, now=NOW,
    )
    assert result["errors"][0]["error"].startswith("invalid_astro_topic")


def test_unknown_source_and_timeout():
    with pytest.raises(ValueError, match="unknown_watch_source"):
        collect_celestial_observations(
            watchlist={"nasa-tv": ("live",)}, transport=fake_transport, now=NOW,
        )
    with pytest.raises(ValueError, match="invalid_http_timeout"):
        collect_celestial_observations(
            watchlist=SMALL_WATCHLIST, transport=fake_transport,
            timeout_seconds=60, now=NOW,
        )


# --- computed astronomy ----------------------------------------------------

def test_amavasya_new_moon():
    result = collect_celestial_observations(
        watchlist={PROVIDER_ASTRO: ("lunar",)}, transport=fake_transport, now=AMAVASYA,
    )
    payload = _payloads(result, "ASTRO:LUNAR")[0]
    assert payload["is_amavasya"] is True
    assert payload["is_poornima"] is False
    assert payload["tithi"] == 30
    assert payload["illumination"] == pytest.approx(0.0, abs=0.01)
    assert "Amavasya" in payload["title"]
    assert payload["market_channel"] == "behavioral_research"


def test_poornima_full_moon():
    result = collect_celestial_observations(
        watchlist={PROVIDER_ASTRO: ("lunar",)}, transport=fake_transport, now=POORNIMA,
    )
    payload = _payloads(result, "ASTRO:LUNAR")[0]
    assert payload["is_poornima"] is True
    assert payload["tithi"] == 15
    assert payload["illumination"] == pytest.approx(1.0, abs=0.01)
    assert "Poornima" in payload["title"]
    assert payload["paksha"] == "shukla"


def test_rashi_nakshatra_computed():
    result = collect_celestial_observations(
        watchlist={PROVIDER_ASTRO: ("lunar",)}, transport=fake_transport, now=NOW,
    )
    payload = _payloads(result, "ASTRO:LUNAR")[0]
    assert 0 <= payload["rashi"]["index"] <= 11
    assert 0 <= payload["nakshatra"]["index"] <= 26
    assert 1 <= payload["nakshatra"]["pada"] <= 4
    assert payload["rashi"]["name"]
    assert payload["nakshatra"]["name"]
    assert payload["tithi"] == 28  # krishna paksha, waning moon
    assert payload["paksha"] == "krishna"
    assert payload["precision_note"]


def test_solar_eclipse_window():
    result = collect_celestial_observations(
        watchlist={PROVIDER_ASTRO: ("eclipse",)}, transport=fake_transport, now=SOLAR_ECLIPSE,
    )
    payload = _payloads(result, "ASTRO:ECLIPSE")[0]
    assert payload["solar_window"] is True
    assert payload["lunar_window"] is False
    assert payload["approximate_window"] is True
    assert "Solar eclipse window" in payload["title"]


def test_lunar_eclipse_window():
    result = collect_celestial_observations(
        watchlist={PROVIDER_ASTRO: ("eclipse",)}, transport=fake_transport, now=LUNAR_ECLIPSE,
    )
    payload = _payloads(result, "ASTRO:ECLIPSE")[0]
    assert payload["lunar_window"] is True
    assert payload["solar_window"] is False


def test_no_eclipse_window_on_quiet_date():
    result = collect_celestial_observations(
        watchlist={PROVIDER_ASTRO: ("eclipse",)}, transport=fake_transport, now=NOW,
    )
    payload = _payloads(result, "ASTRO:ECLIPSE")[0]
    assert payload["solar_window"] is False
    assert payload["lunar_window"] is False
    assert payload["title"] == "No eclipse window"


def test_mars_opposition_and_close_approach():
    result = collect_celestial_observations(
        watchlist={PROVIDER_ASTRO: ("planets",)}, transport=fake_transport,
        now=MARS_OPPOSITION,
    )
    payload = _payloads(result, "ASTRO:PLANETS")[0]
    mars = next(p for p in payload["planets"] if p["name"] == "Mars")
    assert mars["distance_au"] == pytest.approx(0.373, abs=0.01)
    assert mars["elongation_deg"] == pytest.approx(178.4, abs=1.0)
    assert mars["opposition"] is True
    assert mars["close_approach_to_earth"] is True
    assert any("Mars: opposition" in event for event in payload["events"])
    assert any("Mars: close approach" in event for event in payload["events"])
    assert "opposition" in payload["title"]


def test_planets_nominal_on_quiet_date():
    result = collect_celestial_observations(
        watchlist={PROVIDER_ASTRO: ("planets",)}, transport=fake_transport, now=PLANETS_QUIET,
    )
    payload = _payloads(result, "ASTRO:PLANETS")[0]
    assert payload["events"] == []
    assert payload["title"] == "Planetary geometry nominal"
    assert len(payload["planets"]) == 7  # Mercury..Neptune


# --- exposure-map wiring ----------------------------------------------------

def test_celestial_exposure_rules():
    storm = {"kind": "geomagnetic", "title": "Geomagnetic storm G2 (Kp 6.67)"}
    assert match_event_exposure(storm) == ["^SPX", "^NDQ", "^DJI"]
    quiet = {"kind": "geomagnetic", "title": "Geomagnetic field quiet (Kp 2.0)"}
    assert match_event_exposure(quiet) == []
    amavasya = {"kind": "lunar", "title": "Amavasya (new moon) — Moon in Meena rashi (tithi 30)"}
    assert match_event_exposure(amavasya) == ["^SPX", "^NDQ", "BTCUSDT"]
    eclipse = {"kind": "eclipse", "title": "Solar eclipse window near new moon (approximate)"}
    assert match_event_exposure(eclipse) == ["^SPX", "^NDQ", "^DJI", "BTCUSDT"]
    flare = {"kind": "xray_flare", "title": "Solar X-ray flux M-class (M2.3)"}
    assert match_event_exposure(flare) == ["^SPX", "^NDQ"]
    planets = {"kind": "planetary", "title": "Mars: opposition window (elongation 178 deg)"}
    assert match_event_exposure(planets) == ["^SPX", "^NDQ", "BTCUSDT"]
    nominal = {"kind": "planetary", "title": "Planetary geometry nominal"}
    assert match_event_exposure(nominal) == []


# --- ingestion gates ---------------------------------------------------------

def test_watch_and_ingest_requires_kill_switch(store):
    report = watch_and_ingest_celestial(
        store, watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW,
    )
    assert report["ingested"] == 0
    assert len(report["rejected"]) == 6
    assert all(item["reason"] == "ingestion_disabled_kill_switch" for item in report["rejected"])
    assert report["live_execution"] is False
    assert report["broker_order_submitted"] is False


def test_watch_and_ingest_happy_path_and_dedup(store, enabled):
    report = watch_and_ingest_celestial(
        store, watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW,
    )
    assert report["fetched"] == 6
    assert report["ingested"] == 6
    assert report["rejected"] == []

    again = watch_and_ingest_celestial(
        store, watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW,
    )
    assert again["ingested"] == 0
    assert again["duplicates_skipped"] == 6


def test_watch_and_ingest_provider_allowlist(store, enabled):
    report = watch_and_ingest_celestial(
        store, allowed_providers={PROVIDER_ASTRO},
        watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW,
    )
    assert report["ingested"] == 3
    assert len(report["rejected"]) == 3
    assert all(item["reason"] == "provider_not_authorized" for item in report["rejected"])
