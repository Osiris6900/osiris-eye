"""Tests for the autonomous research cycle."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.event_market_impact import ExposureRule
from osiris_api.hypothesis_ledger import record_hypothesis
from osiris_api.p2p_ingestion import ENABLE_ENV, IngestionRejection, ingest_p2p_observation
from osiris_api.research_cycle import recent_events_from_store, run_research_cycle

CREATED = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)
LATER = CREATED + timedelta(minutes=30)

PRE_NOISE = ([0.001, -0.001, 0.002, -0.002, 0.001, -0.001, 0.002]
             + [-0.002, 0.001, -0.001, 0.002, -0.002, 0.001])
POST_UP = [0.02] * 12
POST_FLAT = [0.001, -0.001, 0.002, -0.002, 0.001, -0.001,
             0.002, -0.002, 0.001, -0.001, 0.001, -0.001]


def prices_from_returns(start_price, returns):
    prices = [start_price]
    for value in returns:
        prices.append(prices[-1] * (1.0 + value))
    return prices


@pytest.fixture
def store(tmp_path):
    return ResearchStore(str(tmp_path / "cycle.sqlite3"))


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")


def _ingest_one(store, instrument, price, moment):
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


def _ingest_quotes_around(store, instrument, post_returns, pre_returns=PRE_NOISE,
                          created=CREATED):
    pre_prices = prices_from_returns(100.0, pre_returns)
    for index, price in enumerate(pre_prices):
        moment = created - timedelta(minutes=len(pre_prices) - index)
        _ingest_one(store, instrument, price, moment)
    post_prices = prices_from_returns(pre_prices[-1], post_returns)
    for index, price in enumerate(post_prices[1:], start=1):
        _ingest_one(store, instrument, price, created + timedelta(minutes=index - 1))


def _ingest_event(store, instrument, provider, event, observed_at):
    payload = json.dumps(event, separators=(",", ":"), sort_keys=True)
    try:
        ingest_p2p_observation(
            store,
            {
                "schema": "osiris.observation.v1",
                "instrument": instrument,
                "observed_at": observed_at.isoformat(),
                "provider": provider,
                "payload": payload,
                "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
            },
            allowed_providers={provider},
            now=observed_at,
        )
    except IngestionRejection as exc:
        assert str(exc).startswith("store_rejected:duplicate")


def _quake(event_time=CREATED, place="Tokyo, Japan", **extra):
    return {"kind": "earthquake", "place": place,
            "event_time": event_time.isoformat(), **extra}


def _storm(event_time=CREATED + timedelta(minutes=5)):
    return {"kind": "severestorms",
            "title": "Severe storm, Gulf Coast, United States",
            "event_time": event_time.isoformat()}


def _weather_nowhere(event_time=CREATED + timedelta(minutes=10)):
    return {"kind": "weather", "city": "atlantis",
            "event_time": event_time.isoformat()}


def _ingest_quake(store, event=None, suffix="A1", observed_at=None):
    event = event if event is not None else _quake()
    at = observed_at or (CREATED + timedelta(minutes=1))
    _ingest_event(store, f"EQ:USGS:TEST{suffix}", "usgs-earthquakes", event, at)


# --- event scanning ------------------------------------------------------------

def test_recent_events_filters_market_rows(store, enabled):
    _ingest_quotes_around(store, "^NKX", POST_UP)
    _ingest_quake(store)
    _ingest_event(store, "EONET:S1", "nasa-eonet", _storm(),
                  CREATED + timedelta(minutes=6))
    events = recent_events_from_store(store, now=LATER)
    assert [event["kind"] for event in events] == ["severestorms", "earthquake"]


def test_recent_events_lookback_and_future_excluded(store, enabled):
    old = CREATED - timedelta(hours=100)
    _ingest_event(store, "EQ:USGS:OLD", "usgs-earthquakes",
                  _quake(event_time=old), old)
    _ingest_quake(store)
    future = CREATED + timedelta(hours=1)
    _ingest_event(store, "EQ:USGS:FUT", "usgs-earthquakes",
                  _quake(event_time=future), future)
    events = recent_events_from_store(store, now=CREATED)
    assert [event["kind"] for event in events] == ["earthquake"]
    when = events[0]["event_time"]
    assert when == CREATED.isoformat()


def test_recent_events_dedupes_same_event(store, enabled):
    _ingest_quake(store, _quake(mag=5.1), suffix="A1")
    _ingest_quake(store, _quake(mag=5.3), suffix="A2",
                  observed_at=CREATED + timedelta(minutes=2))
    events = recent_events_from_store(store, now=LATER)
    assert len(events) == 1
    assert events[0]["mag"] == 5.3  # newest observation wins


def test_recent_events_validation(store):
    with pytest.raises(ValueError, match="invalid_lookback_hours"):
        recent_events_from_store(store, now=LATER, lookback_hours=0)
    with pytest.raises(ValueError, match="invalid_lookback_hours"):
        recent_events_from_store(store, now=LATER, lookback_hours=745)
    with pytest.raises(ValueError, match="invalid_max_events"):
        recent_events_from_store(store, now=LATER, max_events=0)
    with pytest.raises(ValueError, match="invalid_max_events"):
        recent_events_from_store(store, now=LATER, max_events=101)
    with pytest.raises(ValueError, match="invalid_cycle_time"):
        recent_events_from_store(store, now=datetime(2026, 11, 6, 12, 0, 0))


def test_recent_events_max_events_bound(store, enabled):
    _ingest_quake(store)
    _ingest_event(store, "EONET:S1", "nasa-eonet", _storm(),
                  CREATED + timedelta(minutes=6))
    _ingest_event(store, "WX:ATLANTIS", "open-meteo", _weather_nowhere(),
                  CREATED + timedelta(minutes=11))
    events = recent_events_from_store(store, now=LATER, max_events=2)
    assert [event["kind"] for event in events] == ["weather", "severestorms"]


# --- full cycle ------------------------------------------------------------------

def test_cycle_empty_store(store):
    report = run_research_cycle(store, now=LATER)
    assert report == {
        "cycle": "research_cycle",
        "ran_at": LATER.isoformat(),
        "lookback_hours": 72,
        "events_scanned": 0,
        "events_skipped_invalid": 0,
        "verdicts": {},
        "assessed_events": [],
        "hypotheses_recorded": 0,
        "evaluations": {"evaluated": 0, "still_pending": 0, "already_evaluated": 0},
        "outcomes": {},
        "trust_labels": {},
        "rules": {},
        "hypothesis": True,
        "causality": "not_established",
        "note": ("event-aligned reactions and follow-through statistics of "
                 "past hypotheses; not causality and not a guarantee"),
        "live_execution": False,
    }


def test_cycle_significant_event_records_and_evaluates(store, enabled):
    _ingest_quotes_around(store, "^NKX", POST_UP)
    _ingest_quake(store)
    _ingest_event(store, "EONET:S1", "nasa-eonet", _storm(),
                  CREATED + timedelta(minutes=6))
    report = run_research_cycle(store, now=LATER)
    assert report["events_scanned"] == 2
    assert report["verdicts"] == {
        "significant_reaction": 1,
        "insufficient_aligned_data": 1,
    }
    assert report["hypotheses_recorded"] == 1
    assert report["evaluations"] == {
        "evaluated": 1, "still_pending": 0, "already_evaluated": 0,
    }
    assert report["outcomes"] == {"followed": 1}
    rules = report["rules"]
    assert rules["earthquake"]["followed"] == 1
    assert rules["earthquake"]["scored"] == 1
    assert rules["earthquake"]["trust"] == "insufficient_samples"


def test_cycle_quiet_event_records_nothing(store, enabled):
    _ingest_quotes_around(store, "^NKX", POST_FLAT)
    _ingest_quake(store)
    report = run_research_cycle(store, now=LATER)
    assert report["verdicts"] == {"no_significant_reaction": 1}
    assert report["hypotheses_recorded"] == 0
    assert report["evaluations"]["evaluated"] == 0
    assert report["outcomes"] == {}
    assert report["rules"] == {}


def test_cycle_event_without_exposure(store, enabled):
    _ingest_event(store, "WX:ATLANTIS", "open-meteo", _weather_nowhere(),
                  CREATED + timedelta(minutes=11))
    report = run_research_cycle(store, now=LATER)
    assert report["verdicts"] == {"no_known_exposure": 1}
    assert report["hypotheses_recorded"] == 0


def test_cycle_skips_invalid_events(store, enabled):
    _ingest_quotes_around(store, "^NKX", POST_UP)
    _ingest_quake(store)
    bloated_rules = (
        ExposureRule("earthquake", ("japan",),
                     tuple(f"INST{index}" for index in range(17))),
    )
    report = run_research_cycle(store, now=LATER, rules=bloated_rules)
    assert report["events_scanned"] == 1
    assert report["events_skipped_invalid"] == 1
    assert report["verdicts"] == {}
    assert report["hypotheses_recorded"] == 0


def test_cycle_idempotent_second_run(store, enabled):
    _ingest_quotes_around(store, "^NKX", POST_UP)
    _ingest_quake(store)
    first = run_research_cycle(store, now=LATER)
    second = run_research_cycle(store, now=LATER + timedelta(minutes=5))
    assert first["hypotheses_recorded"] == 1
    assert first["evaluations"]["evaluated"] == 1
    assert second["hypotheses_recorded"] == 0
    assert second["evaluations"]["evaluated"] == 0
    assert second["evaluations"]["already_evaluated"] == 1


def test_cycle_surfaces_trust_labels(store, enabled):
    _ingest_quotes_around(store, "BTCUSDT", POST_UP)
    for offset in range(5):
        record_hypothesis(
            store, rule_label="lunar", instrument="BTCUSDT",
            direction="up", created=CREATED + timedelta(seconds=offset),
            horizon_points=10,
        )
    report = run_research_cycle(store, now=LATER)
    assert report["events_scanned"] == 0
    assert report["outcomes"] == {"followed": 5}
    assert report["rules"]["lunar"]["hit_rate"] == 1.0
    assert report["rules"]["lunar"]["trust"] == "promising"
    assert report["trust_labels"] == {"promising": 1}


def test_cycle_honesty_flags(store, enabled):
    _ingest_quotes_around(store, "^NKX", POST_UP)
    _ingest_quake(store)
    report = run_research_cycle(store, now=LATER)
    assert report["hypothesis"] is True
    assert report["causality"] == "not_established"
    assert report["live_execution"] is False
    assert "not causality" in report["note"]


def test_cycle_validation(store):
    with pytest.raises(ValueError, match="invalid_cycle_time"):
        run_research_cycle(store, now=datetime(2026, 11, 6, 12, 0, 0))
    with pytest.raises(ValueError, match="invalid_horizon_points"):
        run_research_cycle(store, now=LATER, horizon_points=1)
    with pytest.raises(ValueError, match="invalid_min_samples"):
        run_research_cycle(store, now=LATER, min_samples=0)
    with pytest.raises(ValueError, match="invalid_lookback_hours"):
        run_research_cycle(store, now=LATER, lookback_hours=0)
    with pytest.raises(ValueError, match="invalid_max_events"):
        run_research_cycle(store, now=LATER, max_events=0)
