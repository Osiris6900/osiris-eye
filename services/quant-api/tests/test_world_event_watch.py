"""Tests for the world-event watch layer (keyless public world signals)."""
import hashlib
import json
from datetime import datetime, timezone

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.p2p_ingestion import ENABLE_ENV
from osiris_api.world_event_watch import (
    DEFAULT_WORLD_WATCHLIST,
    PROVIDER_EONET,
    PROVIDER_OPEN_METEO,
    PROVIDER_USGS,
    collect_world_observations,
    watch_and_ingest_world,
)

NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)

USGS_GEOJSON = json.dumps({
    "type": "FeatureCollection",
    "features": [
        {
            "id": "us7000abc",
            "properties": {
                "mag": 5.4, "place": "12km SSE of Tokyo, Japan",
                "time": 1762444800000, "type": "earthquake",
                "id": "us7000abc", "tsunami": 0, "alert": "green", "felt": 12,
            },
            "geometry": {"type": "Point", "coordinates": [139.7, 35.6, 40.0]},
        },
        {
            "id": "us7000def",
            "properties": {
                "mag": None, "place": "Offshore California",
                "time": 1762444900000, "type": "earthquake", "id": "us7000def",
            },
            "geometry": {"type": "Point", "coordinates": [-122.5, 37.1, 8.0]},
        },
        {
            "id": "",
            "properties": {"mag": 4.9, "place": "", "time": None, "id": ""},
            "geometry": None,
        },
    ],
}).encode()

EONET_EVENTS = json.dumps({
    "events": [
        {
            "id": "EONET_1234", "title": "Hurricane Test, Florida",
            "categories": [{"id": "severeStorms", "title": "Severe Storms"}],
            "geometry": [{"date": "2026-11-06T06:00:00Z", "type": "Point",
                          "coordinates": [-80.5, 25.2]}],
        },
        {
            "id": "EONET_9999", "title": "Wildfire Test, California",
            "categories": [{"id": "wildfires", "title": "Wildfires"}],
            "geometry": [{"date": "2026-11-05T18:30:00Z", "type": "Point",
                          "coordinates": [-119.4, 36.7]}],
        },
    ],
}).encode()

OPEN_METEO_CURRENT = json.dumps({
    "latitude": 40.7143, "longitude": -74.006,
    "current": {"time": "2026-11-06T11:45", "temperature_2m": 12.3,
                "precipitation": 0.4, "wind_speed_10m": 18.2},
}).encode()


def fake_transport(url: str, timeout: float) -> bytes:
    if "earthquake.usgs.gov" in url:
        return USGS_GEOJSON
    if "eonet.gsfc.nasa.gov" in url:
        return EONET_EVENTS
    if "open-meteo.com" in url:
        return OPEN_METEO_CURRENT
    raise RuntimeError("watch_source_unavailable")


SMALL_WATCHLIST = {
    PROVIDER_USGS: ("significant_day",),
    PROVIDER_EONET: ("severeStorms",),
    PROVIDER_OPEN_METEO: ("new-york",),
}


@pytest.fixture
def store(tmp_path):
    return ResearchStore(str(tmp_path / "world.sqlite3"))


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")


def test_collect_returns_all_sources():
    result = collect_world_observations(
        watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW,
    )
    assert result["errors"] == []
    instruments = [obs["instrument"] for obs in result["observations"]]
    # providers are iterated alphabetically: nasa-eonet, open-meteo, usgs
    # two usable quakes (third feature lacks identity), two eonet events, one city
    assert instruments == [
        "EONET:EONET_1234", "EONET:EONET_9999", "WX:NEW-YORK",
        "EQ:USGS:US7000ABC", "EQ:USGS:US7000DEF",
    ]
    for observation in result["observations"]:
        assert observation["schema"] == "osiris.observation.v1"
        assert observation["observed_at"] == NOW.isoformat()
        assert observation["payload_sha256"] == hashlib.sha256(
            observation["payload"].encode("utf-8")
        ).hexdigest()


def test_earthquake_payload_normalized():
    result = collect_world_observations(
        watchlist={PROVIDER_USGS: ("significant_day",)},
        transport=fake_transport, now=NOW,
    )
    payload = json.loads(result["observations"][0]["payload"])
    assert payload["kind"] == "earthquake"
    assert payload["magnitude"] == 5.4
    assert payload["place"] == "12km SSE of Tokyo, Japan"
    assert payload["event_time"] == "2025-11-06T16:00:00+00:00"
    assert payload["longitude"] == 139.7
    assert payload["latitude"] == 35.6
    assert payload["depth_km"] == 40.0
    # missing magnitude stays null — never invented
    second = json.loads(result["observations"][1]["payload"])
    assert second["magnitude"] is None
    assert second["place"] == "Offshore California"


def test_eonet_payload_normalized():
    result = collect_world_observations(
        watchlist={PROVIDER_EONET: ("severeStorms",)},
        transport=fake_transport, now=NOW,
    )
    payload = json.loads(result["observations"][0]["payload"])
    assert payload["kind"] == "severestorms"
    assert payload["eonet_id"] == "EONET_1234"
    assert payload["title"] == "Hurricane Test, Florida"
    assert payload["event_time"] == "2026-11-06T06:00:00+00:00"
    assert payload["status"] == "open"


def test_weather_payload_normalized():
    result = collect_world_observations(
        watchlist={PROVIDER_OPEN_METEO: ("new-york",)},
        transport=fake_transport, now=NOW,
    )
    payload = json.loads(result["observations"][0]["payload"])
    assert payload["kind"] == "weather"
    assert payload["city"] == "new-york"
    assert payload["temperature_c"] == 12.3
    assert payload["wind_speed_kmh"] == 18.2
    assert payload["precipitation_mm"] == 0.4


def test_default_watchlist_only_known_sources():
    assert set(DEFAULT_WORLD_WATCHLIST) == {
        PROVIDER_USGS, PROVIDER_EONET, PROVIDER_OPEN_METEO,
    }


def test_unknown_source_rejected():
    with pytest.raises(ValueError, match="unknown_watch_source"):
        collect_world_observations(
            watchlist={"mystery-feed": ("x",)}, transport=fake_transport, now=NOW,
        )


def test_invalid_usgs_feed_rejected():
    result = collect_world_observations(
        watchlist={PROVIDER_USGS: ("significant_year",)},
        transport=fake_transport, now=NOW,
    )
    assert result["observations"] == []
    assert result["errors"][0]["source"] == PROVIDER_USGS
    assert "invalid_usgs_feed" in result["errors"][0]["error"]


def test_invalid_eonet_category_rejected():
    result = collect_world_observations(
        watchlist={PROVIDER_EONET: ("alienInvasion",)},
        transport=fake_transport, now=NOW,
    )
    assert result["errors"][0]["error"].startswith("invalid_eonet_category")


def test_unknown_city_rejected():
    result = collect_world_observations(
        watchlist={PROVIDER_OPEN_METEO: ("atlantis",)},
        transport=fake_transport, now=NOW,
    )
    assert result["errors"][0]["error"].startswith("unknown_city")


def test_source_failure_isolated():
    def broken_transport(url: str, timeout: float) -> bytes:
        if "earthquake.usgs.gov" in url:
            raise RuntimeError("watch_source_unavailable")
        return fake_transport(url, timeout)

    result = collect_world_observations(
        watchlist=SMALL_WATCHLIST, transport=broken_transport, now=NOW,
    )
    assert len(result["errors"]) == 1
    assert result["errors"][0]["source"] == PROVIDER_USGS
    assert len(result["observations"]) == 3  # eonet x2 + weather


def test_malformed_source_reported():
    def garbage_transport(url: str, timeout: float) -> bytes:
        if "eonet.gsfc.nasa.gov" in url:
            return b"not json"
        return fake_transport(url, timeout)

    result = collect_world_observations(
        watchlist=SMALL_WATCHLIST, transport=garbage_transport, now=NOW,
    )
    assert any("invalid_eonet_response" in err["error"] for err in result["errors"])
    assert len(result["observations"]) == 3  # usgs x2 + weather


def test_timeout_bounds_enforced():
    with pytest.raises(ValueError, match="invalid_http_timeout"):
        collect_world_observations(
            watchlist=SMALL_WATCHLIST, transport=fake_transport,
            timeout_seconds=0, now=NOW,
        )


def test_watch_and_ingest_requires_kill_switch(store):
    report = watch_and_ingest_world(
        store, watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW,
    )
    assert report["ingested"] == 0
    assert len(report["rejected"]) == 5
    assert all(item["reason"] == "ingestion_disabled_kill_switch" for item in report["rejected"])
    assert report["live_execution"] is False
    assert report["broker_order_submitted"] is False


def test_watch_and_ingest_happy_path_and_dedup(store, enabled):
    report = watch_and_ingest_world(
        store, watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW,
    )
    assert report["fetched"] == 5
    assert report["ingested"] == 5
    assert report["rejected"] == []

    again = watch_and_ingest_world(
        store, watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW,
    )
    assert again["ingested"] == 0
    assert again["duplicates_skipped"] == 5


def test_watch_and_ingest_provider_allowlist(store, enabled):
    report = watch_and_ingest_world(
        store, allowed_providers={PROVIDER_USGS},
        watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW,
    )
    assert report["ingested"] == 2
    assert len(report["rejected"]) == 3
    assert all(item["reason"] == "provider_not_authorized" for item in report["rejected"])
