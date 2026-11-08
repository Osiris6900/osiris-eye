"""Tests for cross-layer event correlation and the shipping stress index."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.event_correlation import cluster_events, correlation_report
from osiris_api.p2p_ingestion import DATASET, ENABLE_ENV, ingest_p2p_observation
from osiris_api.shipping_stress import shipping_stress_report

NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    return ResearchStore(str(tmp_path / "corr.sqlite3"))


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")


def _observation(instrument: str, provider: str, event: dict, now: datetime) -> dict:
    import hashlib
    payload = json.dumps(event, allow_nan=False, separators=(",", ":"), sort_keys=True)
    return {
        "schema": "osiris.observation.v1",
        "instrument": instrument,
        "observed_at": now.isoformat(),
        "provider": provider,
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


def _ingest(store, instrument, provider, event, now=NOW):
    ingest_p2p_observation(
        store, _observation(instrument, provider, event, now),
        allowed_providers={provider}, now=now,
    )


def test_hurricane_seen_by_three_senses_clusters_together():
    base = NOW - timedelta(hours=2)
    events = [
        {"kind": "severestorms", "title": "Hurricane Example", "latitude": 25.0, "longitude": -80.0,
         "event_time": base.isoformat(), "source": "nasa_eonet_v3"},
        {"kind": "marine_alert", "title": "Hurricane Warning issued", "latitude": 25.4, "longitude": -80.3,
         "event_time": (base + timedelta(minutes=30)).isoformat(), "source": "nws_weather_gov_alerts"},
        {"kind": "buoy_conditions", "place": "Gulf of Mexico buoy 42001", "latitude": 25.2, "longitude": -80.1,
         "event_time": (base + timedelta(hours=1)).isoformat(), "source": "noaa_ndbc_realtime2"},
    ]
    clusters = cluster_events(events)
    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster["members"] == 3
    assert cluster["layers_seen"] == 3
    assert cluster["multi_sense"] is True
    assert cluster["corroboration"] > 0


def test_unrelated_events_stay_separate():
    base = NOW - timedelta(hours=2)
    events = [
        {"kind": "severestorms", "title": "Atlantic storm", "latitude": 25.0, "longitude": -60.0,
         "event_time": base.isoformat(), "source": "nasa_eonet_v3"},
        {"kind": "geomagnetic", "title": "Kp 6 storm", "latitude": None, "longitude": None,
         "event_time": base.isoformat(), "source": "noaa_swpc"},
    ]
    clusters = cluster_events(events)
    assert len(clusters) == 2
    assert all(not cluster["multi_sense"] for cluster in clusters)


def test_far_apart_same_kind_does_not_merge():
    base = NOW - timedelta(hours=2)
    events = [
        {"kind": "severestorms", "title": "Atlantic storm", "latitude": 25.0, "longitude": -60.0,
         "event_time": base.isoformat(), "source": "nasa_eonet_v3"},
        {"kind": "marine_alert", "title": "Pacific storm warning", "latitude": 35.0, "longitude": 150.0,
         "event_time": base.isoformat(), "source": "nws_weather_gov_alerts"},
    ]
    clusters = cluster_events(events)
    assert len(clusters) == 2


def test_time_window_boundary():
    base = NOW - timedelta(hours=30)
    events = [
        {"kind": "severestorms", "title": "Old storm", "latitude": 25.0, "longitude": -60.0,
         "event_time": base.isoformat(), "source": "nasa_eonet_v3"},
        {"kind": "marine_alert", "title": "New warning", "latitude": 25.1, "longitude": -60.1,
         "event_time": NOW.isoformat(), "source": "nws_weather_gov_alerts"},
    ]
    clusters = cluster_events(events, window_hours=24)
    assert len(clusters) == 2
    merged = cluster_events(events, window_hours=48)
    assert len(merged) == 1


def test_correlation_report_from_store(store, enabled):
    base = NOW - timedelta(hours=2)
    _ingest(store, "EONET:STORM1", "nasa-eonet",
            {"kind": "severestorms", "title": "Hurricane Example", "latitude": 25.0,
             "longitude": -80.0, "event_time": base.isoformat(), "source": "nasa_eonet_v3"})
    _ingest(store, "ALRT:WARN1", "nws-alerts",
            {"kind": "marine_alert", "title": "Hurricane Warning", "latitude": 25.2,
             "longitude": -80.1, "event_time": base.isoformat(), "source": "nws_weather_gov_alerts"})
    report = correlation_report(store, now=NOW, lookback_hours=72)
    assert report["events_scanned"] == 2
    assert report["multi_sense_clusters"] == 1
    assert report["live_execution"] is False


def _seed_marine(store, site, wave, current_vel, now=NOW):
    _ingest(store, f"SEA:{site.upper()}", "open-meteo-marine",
            {"kind": "marine_conditions", "site": site, "place": f"{site} shipping chokepoint",
             "latitude": 1.0, "longitude": 100.0, "wave_height_m": wave,
             "ocean_current_velocity_kmh": current_vel,
             "event_time": (now - timedelta(minutes=30)).isoformat(),
             "source": "open_meteo_marine"})


def test_shipping_stress_calm_sea(store, enabled):
    _seed_marine(store, "hormuz", 0.5, 0.8)
    report = shipping_stress_report(store, now=NOW, sites=("hormuz",))
    (lane,) = report["lanes"]
    assert lane["status"] == "scored"
    assert lane["score"] < 25
    assert lane["level"] == "calm"


def test_shipping_stress_rough_sea_with_alert(store, enabled):
    _seed_marine(store, "hormuz", 7.5, 4.0)
    _ingest(store, "ALRT:HORMUZ1", "nws-alerts",
            {"kind": "marine_alert", "title": "Storm Warning for the Persian Gulf and Hormuz",
             "place": "Persian Gulf", "event_time": (NOW - timedelta(minutes=20)).isoformat(),
             "source": "nws_weather_gov_alerts"})
    report = shipping_stress_report(store, now=NOW, sites=("hormuz",))
    (lane,) = report["lanes"]
    assert lane["score"] == pytest.approx(100.0)
    assert lane["level"] == "severe"
    assert lane["components"]["alert_points"] == pytest.approx(30.0)
    assert lane["active_alerts"] == 1


def test_shipping_stress_missing_data_is_blind_not_zero(store, enabled):
    report = shipping_stress_report(store, now=NOW, sites=("hormuz",))
    (lane,) = report["lanes"]
    assert lane["status"] == "no_data"
    assert "score" not in lane
    assert report["blind"] == 1
    assert report["fleet_stress"] is None


def test_shipping_stress_stale_reading_is_blind(store, enabled):
    _seed_marine(store, "malacca", 5.0, 2.0,
                 now=NOW - timedelta(hours=12))
    report = shipping_stress_report(store, now=NOW, sites=("malacca",))
    (lane,) = report["lanes"]
    assert lane["status"] == "no_data"


def test_shipping_stress_fleet_average(store, enabled):
    _seed_marine(store, "hormuz", 6.0, 3.0)
    _seed_marine(store, "malacca", 0.3, 0.5)
    report = shipping_stress_report(store, now=NOW, sites=("hormuz", "malacca"))
    assert report["scored"] == 2
    assert report["fleet_stress"] is not None
    assert 0 < report["fleet_stress"] < 100
    assert report["live_execution"] is False


def test_shipping_stress_unknown_lane_rejected(store, enabled):
    with pytest.raises(ValueError, match="unknown_lane"):
        shipping_stress_report(store, now=NOW, sites=("bermuda-triangle",))
