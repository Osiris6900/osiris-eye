"""Tests for the ocean watch layer (seas, buoys, tides, marine alerts)."""
import json
from datetime import datetime, timezone

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.event_market_impact import match_event_exposure
from osiris_api.ocean_watch import (
    DEFAULT_OCEAN_WATCHLIST,
    PROVIDER_COOPS,
    PROVIDER_MARINE,
    PROVIDER_NDBC,
    PROVIDER_NWS,
    collect_ocean_observations,
    watch_and_ingest_ocean,
)
from osiris_api.p2p_ingestion import ENABLE_ENV

NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)

MARINE_JSON = json.dumps({
    "latitude": 1.29, "longitude": 103.79,
    "current": {
        "time": "2026-11-08T11:00", "interval": 900,
        "wave_height": 1.42, "wave_period": 7.5,
        "sea_surface_temperature": 29.8, "ocean_current_velocity": 1.2,
    },
}).encode()

NDBC_TXT = (
    "#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE\n"
    "#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft\n"
    "2026 11 08 11 50 240  9.4 11.0   3.8    11   8.6 267 1019.8  17.3  17.5  15.5   MM   MM    MM\n"
    "2026 11 08 11 40 240  9.0 10.0   3.7    10   8.4 269 1019.7  17.2  17.6  16.1   MM   MM    MM\n"
).encode()

NDBC_MM_ONLY_TXT = (
    "#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE\n"
    "2026 11 08 11 50  MM   MM   MM    MM    MM   MM  MM     MM    MM    MM    MM   MM   MM    MM\n"
).encode()

COOPS_JSON = json.dumps({
    "metadata": {"id": "8518750", "name": "The Battery", "lat": "40.7006", "lon": "-74.0142"},
    "data": [{"t": "2026-11-08 11:54", "v": "0.412", "s": "0.021", "f": "1,0,0,0", "q": "p"}],
}).encode()

NWS_JSON = json.dumps({
    "type": "FeatureCollection",
    "features": [{
        "properties": {
            "id": "urn:oid:2.49.0.1.840.0.abc123",
            "event": "Tsunami Warning",
            "headline": "Tsunami Warning issued for the US West Coast",
            "areaDesc": "California coastal areas",
            "severity": "Severe", "certainty": "Observed",
            "onset": "2026-11-08T10:30:00+00:00",
            "expires": "2026-11-08T18:30:00+00:00",
        },
    }],
}).encode()

NWS_EMPTY_JSON = json.dumps({"type": "FeatureCollection", "features": []}).encode()


def fake_transport(url: str, timeout: float) -> bytes:
    if "marine-api.open-meteo.com" in url:
        return MARINE_JSON
    if "ndbc.noaa.gov" in url:
        return NDBC_TXT
    if "tidesandcurrents.noaa.gov" in url:
        return COOPS_JSON
    if "api.weather.gov" in url:
        return NWS_JSON if "Tsunami" in url else NWS_EMPTY_JSON
    raise RuntimeError("watch_source_unavailable")


SMALL_WATCHLIST = {
    PROVIDER_MARINE: ("malacca", "hormuz"),
    PROVIDER_NDBC: ("46059",),
    PROVIDER_COOPS: ("8518750",),
    PROVIDER_NWS: ("Tsunami Warning",),
}


@pytest.fixture
def store(tmp_path):
    return ResearchStore(str(tmp_path / "ocean.sqlite3"))


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")


def test_collect_from_all_sources():
    result = collect_ocean_observations(
        watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW,
    )
    assert result["errors"] == []
    assert len(result["observations"]) == 5  # 2 marine + 1 buoy + 1 tide + 1 alert
    for observation in result["observations"]:
        assert observation["schema"] == "osiris.observation.v1"
        assert observation["observed_at"] == NOW.isoformat()


def test_marine_payload_contents():
    result = collect_ocean_observations(
        watchlist={PROVIDER_MARINE: ("malacca",)}, transport=fake_transport, now=NOW,
    )
    (observation,) = result["observations"]
    assert observation["instrument"] == "SEA:MALACCA"
    payload = json.loads(observation["payload"])
    assert payload["kind"] == "marine_conditions"
    assert payload["wave_height_m"] == pytest.approx(1.42)
    assert payload["sea_surface_temperature_c"] == pytest.approx(29.8)
    assert "malacca" in payload["place"]


def test_ndbc_newest_row_and_mm_handling():
    result = collect_ocean_observations(
        watchlist={PROVIDER_NDBC: ("46059",)}, transport=fake_transport, now=NOW,
    )
    (observation,) = result["observations"]
    assert observation["instrument"] == "BUOY:46059"
    payload = json.loads(observation["payload"])
    assert payload["kind"] == "buoy_conditions"
    assert payload["event_time"].startswith("2026-11-08T11:50")
    assert payload["wave_height_m"] == pytest.approx(3.8)
    assert payload["wind_speed_ms"] == pytest.approx(9.4)
    assert payload["water_temperature_c"] == pytest.approx(17.5)


def test_ndbc_all_missing_row_yields_null_fields_not_crash():
    def transport(url: str, timeout: float) -> bytes:
        return NDBC_MM_ONLY_TXT
    result = collect_ocean_observations(
        watchlist={PROVIDER_NDBC: ("46059",)}, transport=transport, now=NOW,
    )
    (observation,) = result["observations"]
    payload = json.loads(observation["payload"])
    assert payload["wave_height_m"] is None
    assert payload["event_time"].startswith("2026-11-08T11:50")


def test_coops_water_level_payload():
    result = collect_ocean_observations(
        watchlist={PROVIDER_COOPS: ("8518750",)}, transport=fake_transport, now=NOW,
    )
    (observation,) = result["observations"]
    assert observation["instrument"] == "TIDE:8518750"
    payload = json.loads(observation["payload"])
    assert payload["kind"] == "water_level"
    assert payload["water_level_m"] == pytest.approx(0.412)
    assert payload["datum"] == "MSL"
    assert "Battery" in payload["place"]


def test_nws_alert_payload_and_dedupe_across_event_types():
    def transport(url: str, timeout: float) -> bytes:
        return NWS_JSON
    result = collect_ocean_observations(
        watchlist={PROVIDER_NWS: ("Tsunami Warning", "Tsunami Advisory")},
        transport=transport, now=NOW,
    )
    assert len(result["observations"]) == 1  # same alert id listed twice, counted once
    payload = json.loads(result["observations"][0]["payload"])
    assert payload["kind"] == "marine_alert"
    assert payload["severity"] == "Severe"
    assert "Tsunami" in payload["title"]


def test_unknown_marine_site_rejected():
    result = collect_ocean_observations(
        watchlist={PROVIDER_MARINE: ("bermuda-triangle",)},
        transport=fake_transport, now=NOW,
    )
    assert result["observations"] == []
    assert result["errors"][0]["error"] == "unknown_marine_site:bermuda-triangle"


def test_invalid_station_ids_rejected():
    result = collect_ocean_observations(
        watchlist={PROVIDER_NDBC: ("46059;DROP",)}, transport=fake_transport, now=NOW,
    )
    assert result["observations"] == []
    assert result["errors"][0]["error"] == "invalid_ndbc_station"
    result = collect_ocean_observations(
        watchlist={PROVIDER_COOPS: ("851875",)}, transport=fake_transport, now=NOW,
    )
    assert result["observations"] == []
    assert result["errors"][0]["error"] == "invalid_coops_station"


def test_unknown_nws_event_rejected():
    result = collect_ocean_observations(
        watchlist={PROVIDER_NWS: ("Alien Invasion",)}, transport=fake_transport, now=NOW,
    )
    assert result["observations"] == []
    assert result["errors"][0]["error"] == "invalid_nws_event:Alien Invasion"


def test_source_failure_collected_never_fatal():
    def failing(url: str, timeout: float) -> bytes:
        raise RuntimeError("watch_source_unavailable")
    result = collect_ocean_observations(
        watchlist=SMALL_WATCHLIST, transport=failing, now=NOW,
    )
    assert result["observations"] == []
    assert len(result["errors"]) == 4
    assert all(error["error"] == "watch_source_unavailable" for error in result["errors"])


def test_malformed_source_collected_as_error():
    def garbage(url: str, timeout: float) -> bytes:
        return b"not json at all"
    result = collect_ocean_observations(
        watchlist={PROVIDER_MARINE: ("malacca",)}, transport=garbage, now=NOW,
    )
    assert result["observations"] == []
    assert result["errors"][0]["error"] == "invalid_marine_response"


def test_watch_and_ingest_end_to_end(store, enabled):
    result = watch_and_ingest_ocean(
        store, watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW,
    )
    assert result["cycle"] == "ocean_watch"
    assert result["fetched"] == 5
    assert result["ingested"] == 5
    assert result["duplicates_skipped"] == 0
    assert result["rejected"] == []
    assert result["live_execution"] is False
    assert result["broker_order_submitted"] is False

    repeat = watch_and_ingest_ocean(
        store, watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW,
    )
    assert repeat["duplicates_skipped"] == 5
    assert repeat["ingested"] == 0


def test_ingestion_fail_closed_without_kill_switch(store):
    result = watch_and_ingest_ocean(
        store, watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW,
    )
    assert result["ingested"] == 0
    assert len(result["rejected"]) == 5


def test_exposure_rules_route_ocean_events():
    marine = {"kind": "marine_conditions", "place": "hormuz shipping chokepoint"}
    assert match_event_exposure(marine) == ["USD-JPY", "^SPX"]
    malacca = {"kind": "marine_conditions", "place": "malacca shipping chokepoint"}
    assert match_event_exposure(malacca) == ["USD-CNY", "^NKX"]
    buoy = {"kind": "buoy_conditions", "place": "offshore buoy 46059"}
    assert match_event_exposure(buoy) == ["^SPX"]
    tide = {"kind": "water_level", "place": "The Battery, New York"}
    assert match_event_exposure(tide) == ["^SPX", "^DJI"]
    alert = {"kind": "marine_alert", "title": "Tsunami Warning issued", "place": "California"}
    assert match_event_exposure(alert) == ["^SPX", "^NKX", "USD-JPY"]
    unknown = {"kind": "marine_conditions", "place": "nowhere known"}
    assert match_event_exposure(unknown) == []


def test_default_watchlist_is_all_keyless_sources():
    assert set(DEFAULT_OCEAN_WATCHLIST) == {
        PROVIDER_MARINE, PROVIDER_NDBC, PROVIDER_COOPS, PROVIDER_NWS,
    }
