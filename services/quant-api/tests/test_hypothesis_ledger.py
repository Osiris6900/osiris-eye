"""Tests for the hypothesis feedback ledger."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.hypothesis_ledger import (
    LEDGER_DATASET,
    evaluate_due_hypotheses,
    record_from_assessment,
    record_hypothesis,
    rule_hit_rates,
)
from osiris_api.p2p_ingestion import ENABLE_ENV, IngestionRejection, ingest_p2p_observation

CREATED = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)
LATER = CREATED + timedelta(minutes=30)

PRE_NOISE = ([0.001, -0.001, 0.002, -0.002, 0.001, -0.001, 0.002]
             + [-0.002, 0.001, -0.001, 0.002, -0.002, 0.001])
POST_UP = [0.02] * 12
POST_DOWN = [-0.02] * 12
POST_FLAT = [0.001, -0.001, 0.002, -0.002, 0.001, -0.001,
             0.002, -0.002, 0.001, -0.001, 0.001, -0.001]


def prices_from_returns(start_price, returns):
    prices = [start_price]
    for value in returns:
        prices.append(prices[-1] * (1.0 + value))
    return prices


@pytest.fixture
def store(tmp_path):
    return ResearchStore(str(tmp_path / "ledger.sqlite3"))


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")


def _ingest_around(store, instrument, post_returns, pre_returns=PRE_NOISE,
                   created=CREATED):
    """Pre quotes before ``created``, post quotes at/after it."""
    pre_prices = prices_from_returns(100.0, pre_returns)
    for index, price in enumerate(pre_prices):
        moment = created - timedelta(minutes=len(pre_prices) - index)
        _ingest_one(store, instrument, price, moment)
    post_prices = prices_from_returns(pre_prices[-1], post_returns)
    for index, price in enumerate(post_prices[1:], start=1):
        _ingest_one(store, instrument, price, created + timedelta(minutes=index - 1))


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


# --- recording ---------------------------------------------------------------

def test_record_and_duplicate_idempotent(store):
    first = record_hypothesis(
        store, rule_label="lunar", instrument="BTCUSDT",
        direction="up", created=CREATED, horizon_points=10,
    )
    assert first["recorded"] is True
    again = record_hypothesis(
        store, rule_label="lunar", instrument="BTCUSDT",
        direction="up", created=CREATED, horizon_points=10,
    )
    assert again["recorded"] is False
    assert again["reason"] == "duplicate"
    assert again["hypothesis_id"] == first["hypothesis_id"]


def test_record_validation(store):
    with pytest.raises(ValueError, match="invalid_rule_label"):
        record_hypothesis(store, rule_label="  ", instrument="X", direction="up")
    with pytest.raises(ValueError, match="invalid_instrument"):
        record_hypothesis(store, rule_label="r", instrument="", direction="up")
    with pytest.raises(ValueError, match="invalid_direction"):
        record_hypothesis(store, rule_label="r", instrument="X", direction="sideways")
    with pytest.raises(ValueError, match="invalid_horizon_points"):
        record_hypothesis(store, rule_label="r", instrument="X",
                          direction="up", horizon_points=1)
    with pytest.raises(ValueError, match="invalid_ledger_time"):
        record_hypothesis(store, rule_label="r", instrument="X", direction="up",
                          created="not-a-time")
    with pytest.raises(ValueError, match="invalid_ledger_time"):
        record_hypothesis(store, rule_label="r", instrument="X", direction="up",
                          created=datetime(2026, 11, 6, 12, 0, 0))  # naive


def test_record_from_assessment(store):
    assessment = {
        "event_kind": "lunar",
        "event_summary": "Amavasya",
        "event_time": CREATED.isoformat(),
        "assessments": [
            {"instrument": "^SPX", "status": "assessed", "unusual_move": True,
             "direction": "up"},
            {"instrument": "^NDQ", "status": "assessed", "significant": True,
             "direction": "down"},
            {"instrument": "^DJI", "status": "assessed", "unusual_move": False,
             "direction": "up"},
            {"instrument": "BTCUSDT", "status": "no_market_data"},
        ],
    }
    outcome = record_from_assessment(store, assessment)
    assert outcome["recorded"] == 2
    with pytest.raises(ValueError, match="invalid_rule_label"):
        record_from_assessment(store, {"assessments": []})


# --- evaluation ---------------------------------------------------------------

def test_evaluate_followed(store, enabled):
    _ingest_around(store, "BTCUSDT", POST_UP)
    record_hypothesis(store, rule_label="lunar", instrument="BTCUSDT",
                      direction="up", created=CREATED, horizon_points=10)
    report = evaluate_due_hypotheses(store, now=LATER)
    assert report["evaluated"] == 1
    assert report["still_pending"] == 0
    outcome = report["outcomes"][0]
    assert outcome["outcome"] == "followed"
    assert outcome["direction"] == "up"
    assert outcome["forward_cumulative_return"] > outcome["expectation_band"]


def test_evaluate_reversed(store, enabled):
    _ingest_around(store, "BTCUSDT", POST_DOWN)
    record_hypothesis(store, rule_label="lunar", instrument="BTCUSDT",
                      direction="up", created=CREATED, horizon_points=10)
    report = evaluate_due_hypotheses(store, now=LATER)
    assert report["outcomes"][0]["outcome"] == "reversed"


def test_evaluate_flat(store, enabled):
    _ingest_around(store, "BTCUSDT", POST_FLAT)
    record_hypothesis(store, rule_label="lunar", instrument="BTCUSDT",
                      direction="up", created=CREATED, horizon_points=10)
    report = evaluate_due_hypotheses(store, now=LATER)
    assert report["outcomes"][0]["outcome"] == "flat"


def test_evaluate_still_pending(store, enabled):
    _ingest_around(store, "BTCUSDT", [0.02, 0.02, 0.02])  # only 3 post points
    record_hypothesis(store, rule_label="lunar", instrument="BTCUSDT",
                      direction="up", created=CREATED, horizon_points=10)
    report = evaluate_due_hypotheses(store, now=LATER)
    assert report["evaluated"] == 0
    assert report["still_pending"] == 1
    assert report["outcomes"] == []


def test_evaluate_expired_without_baseline(store, enabled):
    _ingest_around(store, "BTCUSDT", POST_UP, pre_returns=[])  # no pre quotes
    record_hypothesis(store, rule_label="lunar", instrument="BTCUSDT",
                      direction="up", created=CREATED, horizon_points=10)
    report = evaluate_due_hypotheses(store, now=LATER)
    assert report["outcomes"][0]["outcome"] == "expired_insufficient_baseline"


def test_evaluate_idempotent(store, enabled):
    _ingest_around(store, "BTCUSDT", POST_UP)
    record_hypothesis(store, rule_label="lunar", instrument="BTCUSDT",
                      direction="up", created=CREATED, horizon_points=10)
    first = evaluate_due_hypotheses(store, now=LATER)
    second = evaluate_due_hypotheses(store, now=LATER + timedelta(minutes=5))
    assert first["evaluated"] == 1
    assert second["evaluated"] == 0
    assert second["already_evaluated"] == 1


def test_evaluate_empty_store(store):
    report = evaluate_due_hypotheses(store, now=LATER)
    assert report == {
        "evaluated": 0, "still_pending": 0, "already_evaluated": 0,
        "outcomes": [], "evaluated_at": LATER.isoformat(),
        "live_execution": False,
    }


# --- hit rates -----------------------------------------------------------------

def _score_rule(store, label, count, direction, created=CREATED):
    for offset in range(count):
        record_hypothesis(
            store, rule_label=label, instrument="BTCUSDT",
            direction=direction,
            created=created + timedelta(seconds=offset),
            horizon_points=10,
        )


def test_rule_hit_rates_promising_and_noise(store, enabled):
    _ingest_around(store, "BTCUSDT", POST_UP)
    _score_rule(store, "lunar", 5, "up")     # market went up → followed
    _score_rule(store, "bogus-rule", 5, "down")  # wrong way → reversed
    _score_rule(store, "young-rule", 2, "up")
    evaluate_due_hypotheses(store, now=LATER)
    report = rule_hit_rates(store)
    rules = report["rules"]
    assert rules["lunar"]["scored"] == 5
    assert rules["lunar"]["hit_rate"] == 1.0
    assert rules["lunar"]["trust"] == "promising"
    assert rules["bogus-rule"]["scored"] == 5
    assert rules["bogus-rule"]["hit_rate"] == 0.0
    assert rules["bogus-rule"]["trust"] == "noise"
    assert rules["young-rule"]["trust"] == "insufficient_samples"
    assert "not causality" in report["note"]


def test_rule_hit_rates_pending_counted(store):
    record_hypothesis(store, rule_label="lunar", instrument="BTCUSDT",
                      direction="up", created=CREATED, horizon_points=10)
    report = rule_hit_rates(store)
    assert report["rules"]["lunar"]["pending"] == 1
    assert report["rules"]["lunar"]["trust"] == "insufficient_samples"


def test_rule_hit_rates_min_samples_validation(store):
    with pytest.raises(ValueError, match="invalid_min_samples"):
        rule_hit_rates(store, min_samples=0)


def test_ledger_uses_own_dataset(store):
    record_hypothesis(store, rule_label="lunar", instrument="BTCUSDT",
                      direction="up", created=CREATED)
    rows = store.list(LEDGER_DATASET, limit=10)
    assert len(rows) == 1
    assert rows[0]["record_key"].startswith("HYPO:")
    assert rows[0]["payload"]["status"] == "pending"
