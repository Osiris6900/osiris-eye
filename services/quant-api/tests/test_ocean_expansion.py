"""Tests for ocean watch expansion: JMA bulletins + anomaly detection."""
import json
from datetime import datetime, timezone

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.event_market_impact import match_event_exposure
from osiris_api.ocean_anomaly import (
    PROVIDER_ANOMALY,
    detect_ocean_anomalies,
    ingest_ocean_anomalies,
)
from osiris_api.ocean_watch import (
    PROVIDER_JMA,
    collect_ocean_observations,
    watch_and_ingest_ocean,
)
from osiris_api.p2p_ingestion import DATASET, ENABLE_ENV

NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)

JMA_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<feed xmlns="http://www.w3.org/2005/Atom" lang="ja">'
    "<title>高頻度（地震火山）</title>"
    "<entry><title>津波警報・注意報・予報a</title>"
    '<link href="https://www.data.jma.go.jp/developer/xml/data/tsunami1.xml"/>'
    "<updated>2026-11-08T11:30:00+09:00</updated>"
    "<id>urn:jma:tsunami:1</id></entry>"
    "<entry><title>降灰予報（定時）</title>"
    '<link href="https://www.data.jma.go.jp/developer/xml/data/ash1.xml"/>'
    "<updated>2026-11-08T11:00:00+09:00</updated>"
    "<id>urn:jma:ash:1</id></entry>"
    "<entry><title>震源・震度に関する情報</title>"
    '<link href="https://www.data.jma.go.jp/developer/xml/data/quake1.xml"/>'
    "<updated>2026-11-08T10:45:00+09:00</updated>"
    "<id>urn:jma:quake:1</id></entry>"
    "</feed>"
).encode()


def fake_transport(url: str, timeout: float) -> bytes:
    if "jma.go.jp" in url:
        return JMA_XML
    raise RuntimeError("watch_source_unavailable")


@pytest.fixture
def store(tmp_path):
    return ResearchStore(str(tmp_path / "oceanexp.sqlite3"))


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


def _seed_baseline(store, now, values, metric="wave_height_m"):
    """Ingest a buoy history of 9 readings then one latest value."""
    from osiris_api.p2p_ingestion import ingest_p2p_observation
    from datetime import timedelta
    for index, value in enumerate(values):
        event = {
            "kind": "buoy_conditions",
            "place": "offshore buoy 46059",
            "station": "46059",
            "event_time": (now - timedelta(minutes=10 * (len(values) - index))).isoformat(),
            metric: value,
            "source": "noaa_ndbc_realtime2",
        }
        ingest_p2p_observation(
            store, _observation("BUOY:46059", "noaa-ndbc", event, now),
            allowed_providers={"noaa-ndbc"}, now=now,
        )


def test_jma_bulletins_collected():
    result = collect_ocean_observations(
        watchlist={PROVIDER_JMA: ("tsunami", "ashfall", "earthquake")},
        transport=fake_transport, now=NOW,
    )
    assert result["errors"] == []
    assert len(result["observations"]) == 3
    titles = [json.loads(obs["payload"])["title"] for obs in result["observations"]]
    assert any("津波" in title for title in titles)
    tsunami = next(obs for obs in result["observations"]
                   if "津波" in json.loads(obs["payload"])["title"])
    payload = json.loads(tsunami["payload"])
    assert payload["kind"] == "jma_bulletin"
    assert payload["category"] == "tsunami"
    assert payload["event_time"].startswith("2026-11-08T02:30")  # 11:30 JST = 02:30 UTC


def test_jma_category_filter_and_unknown():
    result = collect_ocean_observations(
        watchlist={PROVIDER_JMA: ("tsunami",)}, transport=fake_transport, now=NOW,
    )
    assert len(result["observations"]) == 1
    result = collect_ocean_observations(
        watchlist={PROVIDER_JMA: ("godzilla",)}, transport=fake_transport, now=NOW,
    )
    assert result["errors"][0]["error"] == "invalid_jma_category:godzilla"


def test_jma_ingest_and_dedupe(store, enabled):
    watchlist = {PROVIDER_JMA: ("tsunami",)}
    result = watch_and_ingest_ocean(store, watchlist=watchlist, transport=fake_transport, now=NOW)
    assert result["ingested"] == 1
    repeat = watch_and_ingest_ocean(store, watchlist=watchlist, transport=fake_transport, now=NOW)
    assert repeat["duplicates_skipped"] == 1


def test_jma_exposure_rules():
    tsunami = {"kind": "jma_bulletin", "title": "津波警報・注意報・予報a"}
    assert match_event_exposure(tsunami) == ["^NKX", "USD-JPY"]
    ashfall = {"kind": "jma_bulletin", "title": "降灰予報（定時）"}
    assert match_event_exposure(ashfall) == ["^NKX"]


def test_anomaly_detects_spike(store, enabled):
    _seed_baseline(store, NOW, [1.0, 1.1, 0.9, 1.0, 1.1, 1.0, 0.9, 1.0, 1.1, 9.5])
    report = detect_ocean_anomalies(store)
    spiked = [item for item in report["series_reports"] if item["status"] == "anomaly"]
    assert len(spiked) == 1
    assert spiked[0]["metric"] == "wave_height_m"
    assert spiked[0]["z_score"] >= 3.0
    assert spiked[0]["latest_value"] == pytest.approx(9.5)


def test_anomaly_never_flags_thin_baseline(store, enabled):
    _seed_baseline(store, NOW, [1.0, 9.5])  # only 2 points — far below minimum
    report = detect_ocean_anomalies(store)
    assert report["anomalies"] == []
    assert all(item["status"] == "baseline_building" for item in report["series_reports"])


def test_anomaly_normal_sea_stays_quiet(store, enabled):
    _seed_baseline(store, NOW, [1.0, 1.1, 0.9, 1.0, 1.1, 1.0, 0.9, 1.0, 1.1, 1.0])
    report = detect_ocean_anomalies(store)
    assert report["anomalies"] == []
    assert report["series_reports"][0]["status"] == "normal"


def test_ingest_anomaly_end_to_end(store, enabled):
    _seed_baseline(store, NOW, [1.0, 1.1, 0.9, 1.0, 1.1, 1.0, 0.9, 1.0, 1.1, 9.5])
    result = ingest_ocean_anomalies(store, now=NOW)
    assert result["anomalies_detected"] == 1
    assert result["ingested"] == 1
    assert result["live_execution"] is False
    repeat = ingest_ocean_anomalies(store, now=NOW)
    assert repeat["duplicates_skipped"] == 1


def test_anomaly_fail_closed_without_kill_switch(store, monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    _seed_baseline(store, NOW, [1.0, 1.1, 0.9, 1.0, 1.1, 1.0, 0.9, 1.0, 1.1, 9.5])
    monkeypatch.delenv(ENABLE_ENV)  # kill switch off for the anomaly cycle
    result = ingest_ocean_anomalies(store, now=NOW)
    assert result["anomalies_detected"] == 1
    assert result["ingested"] == 0
    assert len(result["rejected"]) == 1


def test_anomaly_exposure_rules():
    anomaly = {"kind": "ocean_anomaly", "place": "SEA:HORMUZ"}
    assert match_event_exposure(anomaly) == ["USD-JPY", "^SPX"]
