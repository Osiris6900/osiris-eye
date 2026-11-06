"""Tests for event-time aligned impact with significance checks."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.event_market_impact import (
    aligned_event_impact,
    timestamped_returns_from_store,
)
from osiris_api.p2p_ingestion import ENABLE_ENV, IngestionRejection, ingest_p2p_observation

BASE = datetime(2026, 11, 6, 10, 0, 0, tzinfo=timezone.utc)
#: quotes at BASE..BASE+14min; event falls between quote 10 and quote 11,
#: so 10 returns are pre-event (stamped 10:01..10:10) and 4 post (10:11..10:14).
EVENT_TIME = BASE + timedelta(minutes=10, seconds=30)

PRE_NOISE = [0.001, -0.001, 0.002, -0.002, 0.001, -0.001, 0.002, -0.002, 0.001, -0.001]
POST_JUMP = [0.03, 0.028, 0.032, 0.029]
POST_CALM = [0.001, -0.001, 0.002, -0.002]


def prices_from_returns(start_price, returns):
    prices = [start_price]
    for value in returns:
        prices.append(prices[-1] * (1.0 + value))
    return prices


def japan_quake(event_time=EVENT_TIME):
    return {
        "kind": "earthquake", "usgs_id": "us7000abc", "magnitude": 5.4,
        "place": "12km SSE of Tokyo, Japan",
        "event_time": event_time.isoformat(),
        "source": "usgs_geojson_feed",
    }


@pytest.fixture
def store(tmp_path):
    return ResearchStore(str(tmp_path / "aligned.sqlite3"))


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")


def _ingest_prices(store, instrument, prices, start=BASE):
    for index, price in enumerate(prices):
        moment = start + timedelta(minutes=index)
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


def test_timestamped_returns_stamp_later_quote(store, enabled):
    _ingest_prices(store, "^NKX", [100.0, 101.0, 103.0])
    series = timestamped_returns_from_store(store, ["^NKX"])["^NKX"]
    assert [stamp for stamp, _ in series] == [
        (BASE + timedelta(minutes=1)).isoformat(),
        (BASE + timedelta(minutes=2)).isoformat(),
    ]
    assert series[0][1] == pytest.approx(0.01)
    assert series[1][1] == pytest.approx(103.0 / 101.0 - 1)


def test_aligned_significant_reaction(store, enabled):
    _ingest_prices(store, "^NKX", prices_from_returns(100.0, PRE_NOISE + POST_JUMP))
    report = aligned_event_impact(store, japan_quake())
    assert report["verdict"] == "significant_reaction"
    assert report["alignment"] == "event_time_split"
    nkx = next(a for a in report["assessments"] if a["instrument"] == "^NKX")
    assert nkx["status"] == "assessed"
    assert nkx["pre_points"] == 10
    assert nkx["post_points"] == 4
    assert nkx["significant"] is True
    assert nkx["direction"] == "up"
    assert nkx["welch_t"] >= 2.0
    assert nkx["post_cumulative_return"] == pytest.approx(
        1.03 * 1.028 * 1.032 * 1.029 - 1.0, rel=1e-6,
    )
    missing = next(a for a in report["assessments"] if a["instrument"] == "USD-JPY")
    assert missing["status"] == "no_market_data"
    assert report["causality"] == "not_established"
    assert report["hypothesis"] is True
    assert report["live_execution"] is False


def test_aligned_quiet_market_not_significant(store, enabled):
    _ingest_prices(store, "^NKX", prices_from_returns(100.0, PRE_NOISE + POST_CALM))
    report = aligned_event_impact(store, japan_quake())
    assert report["verdict"] == "no_significant_reaction"
    nkx = next(a for a in report["assessments"] if a["instrument"] == "^NKX")
    assert nkx["status"] == "assessed"
    assert nkx["significant"] is False


def test_aligned_down_move_direction(store, enabled):
    down = [-0.03, -0.028, -0.031, -0.029]
    _ingest_prices(store, "^NKX", prices_from_returns(100.0, PRE_NOISE + down))
    report = aligned_event_impact(store, japan_quake())
    nkx = next(a for a in report["assessments"] if a["instrument"] == "^NKX")
    assert nkx["significant"] is True
    assert nkx["direction"] == "down"


def test_aligned_insufficient_post_data(store, enabled):
    _ingest_prices(store, "^NKX", prices_from_returns(100.0, PRE_NOISE + POST_JUMP))
    late_event = japan_quake(event_time=BASE + timedelta(minutes=13, seconds=30))
    report = aligned_event_impact(store, late_event)
    assert report["verdict"] == "insufficient_aligned_data"
    nkx = next(a for a in report["assessments"] if a["instrument"] == "^NKX")
    assert nkx["status"] == "insufficient_aligned_data"
    assert nkx["post_points"] == 1


def test_aligned_min_post_points_enforced(store, enabled):
    _ingest_prices(store, "^NKX", prices_from_returns(100.0, PRE_NOISE + POST_JUMP))
    report = aligned_event_impact(store, japan_quake(), min_post_points=5)
    assert report["verdict"] == "insufficient_aligned_data"


def test_aligned_no_known_exposure(store, enabled):
    event = {"kind": "spaceweather", "title": "flare",
             "event_time": EVENT_TIME.isoformat()}
    report = aligned_event_impact(store, event)
    assert report["verdict"] == "no_known_exposure"
    assert report["matched_instruments"] == []


def test_aligned_event_time_required(store, enabled):
    with pytest.raises(ValueError, match="aligned_event_time_missing"):
        aligned_event_impact(store, {"kind": "earthquake", "place": "Tokyo, Japan"})
    with pytest.raises(ValueError, match="aligned_event_time_missing"):
        aligned_event_impact(store, {
            "kind": "earthquake", "place": "Tokyo, Japan",
            "event_time": "2026-11-06T10:30:00",  # naive, no timezone
        })
    with pytest.raises(ValueError, match="aligned_event_time_missing"):
        aligned_event_impact(store, {
            "kind": "earthquake", "place": "Tokyo, Japan",
            "event_time": "not-a-time",
        })


def test_aligned_window_bounds(store, enabled):
    with pytest.raises(ValueError, match="invalid_pre_points"):
        aligned_event_impact(store, japan_quake(), pre_points=5)
    with pytest.raises(ValueError, match="invalid_post_points"):
        aligned_event_impact(store, japan_quake(), post_points=1)
    with pytest.raises(ValueError, match="invalid_post_points"):
        aligned_event_impact(store, japan_quake(), post_points=4, min_post_points=8)


def test_aligned_event_time_echoed(store, enabled):
    _ingest_prices(store, "^NKX", prices_from_returns(100.0, PRE_NOISE + POST_CALM))
    report = aligned_event_impact(store, japan_quake())
    assert report["event_time"] == EVENT_TIME.isoformat()
    assert "significance_note" in report
