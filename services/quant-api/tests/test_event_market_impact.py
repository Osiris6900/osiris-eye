"""Tests for event → market impact hypotheses."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.event_market_impact import (
    ExposureRule,
    assess_event_impact,
    market_returns_from_store,
    match_event_exposure,
)
from osiris_api.p2p_ingestion import ENABLE_ENV, IngestionRejection, ingest_p2p_observation

BASE = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)

QUIET = [0.001, -0.001, 0.002, -0.002, 0.001, -0.001, 0.002, -0.002, 0.001, -0.001]
SPIKE = QUIET[:-1] + [0.05]


def japan_quake():
    return {
        "kind": "earthquake", "usgs_id": "us7000abc", "magnitude": 5.4,
        "place": "12km SSE of Tokyo, Japan",
        "event_time": "2026-11-06T10:00:00+00:00",
        "source": "usgs_geojson_feed",
    }


def test_exposure_matches_japan_quake():
    assert match_event_exposure(japan_quake()) == ["^NKX", "USD-JPY"]


def test_exposure_matches_us_storm_from_observation_contract():
    observation = {
        "schema": "osiris.observation.v1",
        "instrument": "EONET:EONET_1234",
        "payload": json.dumps({
            "kind": "severestorms", "title": "Hurricane Test, Florida",
            "categories": ["severeStorms"], "source": "nasa_eonet_v3",
        }),
    }
    assert match_event_exposure(observation) == ["^SPX", "^DJI"]


def test_exposure_case_insensitive_and_deduped():
    event = {"kind": "weather", "city": "New-York"}
    assert match_event_exposure(event) == ["^SPX", "^NDQ", "^DJI"]


def test_exposure_empty_when_nothing_matches():
    assert match_event_exposure({"kind": "earthquake", "place": "Middle of nowhere"}) == []
    assert match_event_exposure({"kind": "spaceweather", "title": "solar flare"}) == []


def test_exposure_rejects_junk():
    with pytest.raises(ValueError, match="invalid_event_payload"):
        match_event_exposure("not json at all {")
    with pytest.raises(ValueError, match="event_kind_missing"):
        match_event_exposure({"place": "Tokyo"})


def test_custom_rules_extendible():
    rules = (ExposureRule("spaceweather", ("solar",), ("^SPX",)),)
    assert match_event_exposure(
        {"kind": "spaceweather", "title": "Solar flare X2"}, rules=rules,
    ) == ["^SPX"]


def test_assess_unusual_move_detected():
    report = assess_event_impact(japan_quake(), {"^NKX": SPIKE}, window=5)
    assert report["verdict"] == "unusual_move_detected"
    nkx = report["assessments"][0]
    assert nkx["instrument"] == "^NKX"
    assert nkx["status"] == "assessed"
    assert nkx["unusual_move"] is True
    assert nkx["direction"] == "up"
    missing = report["assessments"][1]
    assert missing == {"instrument": "USD-JPY", "status": "no_market_data"}
    assert report["hypothesis"] is True
    assert report["causality"] == "not_established"
    assert report["live_execution"] is False


def test_assess_quiet_market_no_unusual_move():
    report = assess_event_impact(
        japan_quake(), {"^NKX": QUIET, "USD-JPY": QUIET}, window=5,
    )
    assert report["verdict"] == "no_unusual_move"
    assert all(item["unusual_move"] is False for item in report["assessments"])


def test_assess_insufficient_and_no_exposure():
    report = assess_event_impact(japan_quake(), {"^NKX": [0.01, 0.02]}, window=5)
    assert report["verdict"] == "insufficient_market_data"
    assert report["assessments"][0]["status"] == "insufficient_data"
    assert report["assessments"][0]["required"] == 10

    nothing = assess_event_impact({"kind": "spaceweather", "title": "flare"}, {}, window=5)
    assert nothing["verdict"] == "no_known_exposure"
    assert nothing["matched_instruments"] == []


def test_assess_window_bounds():
    with pytest.raises(ValueError, match="invalid_window"):
        assess_event_impact(japan_quake(), {}, window=1)


@pytest.fixture
def store(tmp_path):
    return ResearchStore(str(tmp_path / "impact.sqlite3"))


def _ingest_prices(store, monkeypatch, instrument, prices):
    monkeypatch.setenv(ENABLE_ENV, "1")
    for index, price in enumerate(prices):
        moment = BASE + timedelta(minutes=index)
        payload = json.dumps({"last": price, "source": "binance_spot_24hr_ticker"})
        try:
            ingest_p2p_observation(
                store,
                {
                    "schema": "osiris.observation.v1",
                    "instrument": instrument,
                    "observed_at": moment.isoformat(),
                    "provider": "binance-public",
                    "payload": payload,
                    "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
                },
                allowed_providers={"binance-public"},
                now=moment,
            )
        except IngestionRejection as exc:
            assert str(exc).startswith("store_rejected:duplicate")


def test_market_returns_from_store(store, monkeypatch):
    _ingest_prices(store, monkeypatch, "^NKX", [100.0, 101.0, 103.0])
    _ingest_prices(store, monkeypatch, "USD-JPY", [150.0, 150.0])  # duplicate → one quote → omitted
    series_map = market_returns_from_store(store, ["^NKX", "USD-JPY", "^SPX"])
    assert set(series_map) == {"^NKX"}
    returns = series_map["^NKX"]
    assert len(returns) == 2
    assert returns[0] == pytest.approx(0.01)
    assert returns[1] == pytest.approx(103.0 / 101.0 - 1)


def test_market_returns_orders_oldest_first(store, monkeypatch):
    _ingest_prices(store, monkeypatch, "BTCUSDT", [69000.0, 70000.0, 68000.0, 71000.0])
    series_map = market_returns_from_store(store, ["BTCUSDT"], points=3)
    returns = series_map["BTCUSDT"]
    assert len(returns) == 2  # last 3 quotes → 2 returns, oldest dropped
    assert returns[0] == pytest.approx(68000.0 / 70000.0 - 1)
    assert returns[1] == pytest.approx(71000.0 / 68000.0 - 1)


def test_market_returns_validation(store):
    with pytest.raises(ValueError, match="invalid_points"):
        market_returns_from_store(store, ["^NKX"], points=1)
    with pytest.raises(ValueError, match="invalid_instrument"):
        market_returns_from_store(store, ["  "], points=8)


def test_full_cycle_event_to_impact(store, monkeypatch):
    """The whole chain: ingest market quotes, then assess an event against them."""
    # strictly increasing quotes keep payload hashes unique while returns stay tiny
    calm = [100.0 + i * 0.01 for i in range(10)]
    _ingest_prices(store, monkeypatch, "^NKX", calm + [130.0])
    fx = [150.0 + i * 0.01 for i in range(11)]
    _ingest_prices(store, monkeypatch, "USD-JPY", fx)
    series_map = market_returns_from_store(store, ["^NKX", "USD-JPY"], points=64)
    report = assess_event_impact(japan_quake(), series_map, window=5)
    assert report["verdict"] == "unusual_move_detected"
    verdicts = {item["instrument"]: item for item in report["assessments"]}
    assert verdicts["^NKX"]["unusual_move"] is True
    assert verdicts["USD-JPY"]["unusual_move"] is False
