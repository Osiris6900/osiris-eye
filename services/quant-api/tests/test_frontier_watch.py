"""Tests for the frontier watch layer (rivers, radiation, satellites, volcanoes)."""
import json
from datetime import datetime, timezone

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.event_market_impact import match_event_exposure
from osiris_api.frontier_watch import (
    DEFAULT_FRONTIER_WATCHLIST,
    PROVIDER_CELESTRAK,
    PROVIDER_GVP,
    PROVIDER_SAFECAST,
    PROVIDER_USGS_WATER,
    collect_frontier_observations,
    watch_and_ingest_frontier,
)
from osiris_api.p2p_ingestion import ENABLE_ENV

NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)

USGS_WATER_JSON = json.dumps({
    "value": {"timeSeries": [
        {
            "sourceInfo": {"siteName": "POTOMAC RIVER NEAR WASH, DC"},
            "variable": {"variableCode": [{"value": "00060"}]},
            "values": [{"value": [{"value": "1980", "dateTime": "2026-11-08T06:50:00.000-05:00"}]}],
        },
        {
            "sourceInfo": {"siteName": "POTOMAC RIVER NEAR WASH, DC"},
            "variable": {"variableCode": [{"value": "00065"}]},
            "values": [{"value": [{"value": "2.82", "dateTime": "2026-11-08T06:50:00.000-05:00"}]}],
        },
    ]},
}).encode()

SAFECAST_JSON = json.dumps([
    {"id": 101, "value": 42.0, "unit": "cpm", "captured_at": "2026-11-08T11:30:00.000Z",
     "latitude": 37.42, "longitude": 141.03},
    {"id": 100, "value": 39.5, "unit": "cpm", "captured_at": "2026-11-08T10:15:00.000Z",
     "latitude": 37.43, "longitude": 141.02},
]).encode()

CELESTRAK_TLE = (
    "ISS (ZARYA)\n"
    "1 25544U 98067A   26311.46585591  .00014112  00000+0  26146-3 0  9996\n"
    "2 25544  51.6322 311.0744 0007266 358.6454   1.4513 15.49241650593960\n"
    "CSS (TIANHE)\n"
    "1 48274U 21035A   26311.50000000  .00020000  00000+0  20000-3 0  9995\n"
    "2 48274  41.4700 120.0000 0005000 100.0000 260.0000 15.60000000999999\n"
).encode()

GVP_RSS = (
    '<?xml version="1.0"?>\n<rss version="2.0"><channel>'
    "<title>Smithsonian / USGS Weekly Volcanic Activity Report</title>"
    "<item><title>Etna | Sicily (Italy) | New activity/unrest</title>"
    "<link>https://volcano.si.edu/volcano.cfm?vn=211060</link>"
    "<pubDate>Wed, 05 Nov 2026 00:00:00 +0000</pubDate></item>"
    "<item><title>Merapi | Central Java (Indonesia) | New activity/unrest</title>"
    "<link>https://volcano.si.edu/volcano.cfm?vn=263250</link>"
    "<pubDate>Wed, 05 Nov 2026 00:00:00 +0000</pubDate></item>"
    "</channel></rss>"
).encode()


def fake_transport(url: str, timeout: float) -> bytes:
    if "waterservices.usgs.gov" in url:
        return USGS_WATER_JSON
    if "safecast.org" in url:
        return SAFECAST_JSON
    if "celestrak.org" in url:
        return CELESTRAK_TLE
    if "volcano.si.edu" in url:
        return GVP_RSS
    raise RuntimeError("watch_source_unavailable")


SMALL_WATCHLIST = {
    PROVIDER_USGS_WATER: ("01646500",),
    PROVIDER_SAFECAST: ("fukushima",),
    PROVIDER_CELESTRAK: ("stations",),
    PROVIDER_GVP: ("weekly",),
}


@pytest.fixture
def store(tmp_path):
    return ResearchStore(str(tmp_path / "frontier.sqlite3"))


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")


def test_collect_from_all_sources():
    result = collect_frontier_observations(
        watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW,
    )
    assert result["errors"] == []
    # 1 gauge + 1 radiation + 2 satellites + 2 volcanoes = 6
    assert len(result["observations"]) == 6


def test_usgs_water_payload():
    result = collect_frontier_observations(
        watchlist={PROVIDER_USGS_WATER: ("01646500",)}, transport=fake_transport, now=NOW,
    )
    (observation,) = result["observations"]
    assert observation["instrument"] == "RIV:01646500"
    payload = json.loads(observation["payload"])
    assert payload["kind"] == "river_gauge"
    assert payload["discharge_cfs"] == pytest.approx(1980.0)
    assert payload["gage_height_ft"] == pytest.approx(2.82)
    assert payload["event_time"].startswith("2026-11-08T11:50")


def test_safecast_newest_measurement():
    result = collect_frontier_observations(
        watchlist={PROVIDER_SAFECAST: ("fukushima",)}, transport=fake_transport, now=NOW,
    )
    (observation,) = result["observations"]
    assert observation["instrument"] == "RAD:FUKUSHIMA"
    payload = json.loads(observation["payload"])
    assert payload["kind"] == "radiation"
    assert payload["value"] == pytest.approx(42.0)
    assert payload["unit"] == "cpm"
    assert payload["status"] == "measured"


def test_safecast_empty_is_honest_not_invented():
    def transport(url: str, timeout: float) -> bytes:
        return b"[]"
    result = collect_frontier_observations(
        watchlist={PROVIDER_SAFECAST: ("fukushima",)}, transport=transport, now=NOW,
    )
    (observation,) = result["observations"]
    payload = json.loads(observation["payload"])
    assert payload["status"] == "no_recent_measurements"
    assert payload["value"] is None


def test_celestrak_tle_parsing():
    result = collect_frontier_observations(
        watchlist={PROVIDER_CELESTRAK: ("stations",)}, transport=fake_transport, now=NOW,
    )
    assert len(result["observations"]) == 2
    iss = json.loads(result["observations"][0]["payload"])
    assert iss["kind"] == "satellite_tle"
    assert iss["norad_id"] == "25544"
    assert "ISS" in iss["title"]
    assert iss["mean_motion_rev_day"] == pytest.approx(15.4924, abs=1e-3)


def test_gvp_weekly_report():
    result = collect_frontier_observations(
        watchlist={PROVIDER_GVP: ("weekly",)}, transport=fake_transport, now=NOW,
    )
    assert len(result["observations"]) == 2
    etna = json.loads(result["observations"][0]["payload"])
    assert etna["kind"] == "volcano_report"
    assert "Etna" in etna["title"]
    assert etna["event_time"].startswith("2026-11-05")


def test_invalid_inputs_rejected_as_errors():
    result = collect_frontier_observations(
        watchlist={PROVIDER_USGS_WATER: ("0164650",)}, transport=fake_transport, now=NOW,
    )
    assert result["errors"][0]["error"] == "invalid_usgs_water_site"
    result = collect_frontier_observations(
        watchlist={PROVIDER_SAFECAST: ("area51",)}, transport=fake_transport, now=NOW,
    )
    assert result["errors"][0]["error"] == "unknown_radiation_site:area51"
    result = collect_frontier_observations(
        watchlist={PROVIDER_CELESTRAK: ("starlink",)}, transport=fake_transport, now=NOW,
    )
    assert result["errors"][0]["error"] == "invalid_celestrak_group:starlink"


def test_source_failure_collected_never_fatal():
    def failing(url: str, timeout: float) -> bytes:
        raise RuntimeError("watch_source_unavailable")
    result = collect_frontier_observations(
        watchlist=SMALL_WATCHLIST, transport=failing, now=NOW,
    )
    assert result["observations"] == []
    assert len(result["errors"]) == 4


def test_watch_and_ingest_end_to_end(store, enabled):
    result = watch_and_ingest_frontier(
        store, watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW,
    )
    assert result["cycle"] == "frontier_watch"
    assert result["fetched"] == 6
    assert result["ingested"] == 6
    assert result["rejected"] == []
    assert result["live_execution"] is False

    repeat = watch_and_ingest_frontier(
        store, watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW,
    )
    assert repeat["duplicates_skipped"] == 6
    assert repeat["ingested"] == 0


def test_ingestion_fail_closed_without_kill_switch(store):
    result = watch_and_ingest_frontier(
        store, watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW,
    )
    assert result["ingested"] == 0
    assert len(result["rejected"]) == 6


def test_exposure_rules_route_frontier_events():
    gauge = {"kind": "river_gauge", "place": "Mississippi River at Baton Rouge, Louisiana"}
    assert match_event_exposure(gauge) == ["^SPX"]
    radiation = {"kind": "radiation", "place": "fukushima radiation monitor"}
    assert match_event_exposure(radiation) == ["^NKX", "USD-JPY"]
    volcano = {"kind": "volcano_report", "title": "Merapi | Central Java (Indonesia)"}
    assert match_event_exposure(volcano) == ["^NKX"]
    tle = {"kind": "satellite_tle", "title": "ISS (ZARYA)"}
    assert match_event_exposure(tle) == []  # honestly no known exposure


def test_default_watchlist_is_all_keyless_sources():
    assert set(DEFAULT_FRONTIER_WATCHLIST) == {
        PROVIDER_USGS_WATER, PROVIDER_SAFECAST, PROVIDER_CELESTRAK, PROVIDER_GVP,
    }
