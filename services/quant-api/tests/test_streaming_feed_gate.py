"""Deterministic offline acceptance tests for the streaming research feed gate."""
from datetime import datetime, timedelta, timezone

import pytest

from osiris_api.streaming_feed_gate import FeedRejected, StreamingFeedGate

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def payload(**overrides):
    tick = dict(
        provider="licensed",
        instrument_id="BTC-USD",
        price="100.25",
        event_time=NOW - timedelta(seconds=1),
        available_at=NOW,
        sequence=1,
        now=NOW,
    )
    tick.update(overrides)
    return tick


def gate():
    return StreamingFeedGate({"licensed": {"BTC-USD"}}, max_age=timedelta(seconds=5))


def test_accepts_entitled_point_in_time_tick():
    tick = gate().ingest(**payload())
    assert str(tick.price) == "100.25"
    assert tick.available_at == NOW


@pytest.mark.parametrize("changes", [
    {"provider": "unlicensed"},
    {"instrument_id": "UNKNOWN"},
    {"price": "NaN"},
    {"price": "Infinity"},
    {"price": "0"},
    {"price": "-1"},
    {"price": "bad"},
    {"sequence": -1},
    {"sequence": True},
    {"event_time": NOW - timedelta(seconds=10)},
    {"available_at": NOW + timedelta(seconds=3)},
    {"event_time": NOW + timedelta(seconds=3), "available_at": NOW + timedelta(seconds=3)},
    {"event_time": NOW + timedelta(seconds=1), "available_at": NOW},
    {"now": NOW.replace(tzinfo=None)},
])
def test_rejects_invalid_ticks(changes):
    with pytest.raises(FeedRejected):
        gate().ingest(**payload(**changes))


def test_rejects_duplicate_and_out_of_order_without_advancing_state():
    stream = gate()
    stream.ingest(**payload())
    for changes in ({"sequence": 1}, {"sequence": 0}, {"sequence": 2, "event_time": NOW - timedelta(seconds=2)}):
        with pytest.raises(FeedRejected):
            stream.ingest(**payload(**changes))
    assert stream.latest_sequence("licensed", "BTC-USD") == 1
    stream.ingest(**payload(sequence=2))
    assert stream.latest_sequence("licensed", "BTC-USD") == 2


def test_failed_tick_does_not_poison_stream():
    stream = gate()
    with pytest.raises(FeedRejected):
        stream.ingest(**payload(sequence=100, price="NaN"))
    stream.ingest(**payload(sequence=1))
    assert stream.latest_sequence("licensed", "BTC-USD") == 1
