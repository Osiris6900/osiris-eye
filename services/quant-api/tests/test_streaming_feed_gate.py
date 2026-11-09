"""Deterministic offline acceptance tests for the streaming research feed gate."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from osiris_api.streaming_feed_gate import (
    FeedRejected,
    FeedStatus,
    StreamingFeedGate,
    authorize_feed_use,
)

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
    assert stream.gap_open("licensed", "BTC-USD") is False


def test_sequence_gap_latches_without_advancing_and_blocks_later_ticks():
    stream = gate()
    stream.ingest(**payload(sequence=1))
    with pytest.raises(FeedRejected, match="sequence gap"):
        stream.ingest(**payload(sequence=3, event_time=NOW - timedelta(milliseconds=500)))
    assert stream.latest_sequence("licensed", "BTC-USD") == 1
    assert stream.gap_open("licensed", "BTC-USD") is True
    with pytest.raises(FeedRejected, match="resync required"):
        stream.ingest(**payload(sequence=2))
    assert stream.latest_sequence("licensed", "BTC-USD") == 1
    view = stream.status("licensed", "BTC-USD", NOW)
    assert view.state == "gapped"
    assert view.usable is False
    assert view.price is None
    assert view.live_orders_enabled is False


def test_resync_requires_a_newer_snapshot_then_accepts_the_next_tick():
    stream = gate()
    stream.ingest(**payload(sequence=4))
    with pytest.raises(FeedRejected, match="sequence gap"):
        stream.ingest(**payload(sequence=6))
    with pytest.raises(FeedRejected, match="does not advance"):
        stream.resync(**payload(sequence=4))
    assert stream.gap_open("licensed", "BTC-USD") is True
    snapshot = stream.resync(**payload(
        sequence=6,
        price="101.00",
        event_time=NOW - timedelta(milliseconds=200),
    ))
    assert snapshot.sequence == 6
    assert stream.gap_open("licensed", "BTC-USD") is False
    assert stream.status("licensed", "BTC-USD", NOW).state == "fresh"
    stream.ingest(**payload(
        sequence=7,
        event_time=NOW - timedelta(milliseconds=100),
        price="101.50",
    ))
    assert stream.latest_sequence("licensed", "BTC-USD") == 7


def test_gap_on_one_instrument_does_not_close_another():
    stream = StreamingFeedGate({"licensed": {"BTC-USD", "ETH-USD"}}, max_age=timedelta(seconds=5))
    stream.ingest(**payload())
    stream.ingest(**payload(instrument_id="ETH-USD", sequence=1))
    with pytest.raises(FeedRejected, match="sequence gap"):
        stream.ingest(**payload(sequence=4))
    assert stream.status("licensed", "ETH-USD", NOW).state == "fresh"
    assert stream.status("licensed", "BTC-USD", NOW).state == "gapped"


def test_stale_price_is_not_fresh_and_heartbeat_cannot_refresh_it():
    stream = gate()
    stream.ingest(**payload())
    later = NOW + timedelta(seconds=6)
    stale = stream.status("licensed", "BTC-USD", later)
    assert stale.state == "stale"
    assert stale.usable is False
    assert stale.price is None
    assert stale.transport_alive is False
    stream.note_heartbeat(provider="licensed", instrument_id="BTC-USD", now=later)
    after = stream.status("licensed", "BTC-USD", later)
    assert after.state == "stale"
    assert after.price is None
    assert after.transport_alive is True
    decision = authorize_feed_use(after)
    assert decision["accepted"] is False
    assert decision["reason"] == "feed_stale"
    assert decision["price"] is None
    assert decision["live_orders_enabled"] is False
    assert decision["broker_order_submitted"] is False


def test_fresh_status_is_research_only():
    stream = gate()
    stream.ingest(**payload())
    view = stream.status("licensed", "BTC-USD", NOW)
    assert view.state == "fresh"
    assert view.usable is True
    assert str(view.price) == "100.25"
    decision = authorize_feed_use(view)
    assert decision == {
        "accepted": True,
        "reason": "fresh_for_research_only",
        "price": "100.25",
        "sequence": 1,
        "live_orders_enabled": False,
        "broker_order_submitted": False,
    }


def test_heartbeat_before_any_tick_does_not_invent_a_price():
    stream = gate()
    stream.note_heartbeat(provider="licensed", instrument_id="BTC-USD", now=NOW)
    view = stream.status("licensed", "BTC-USD", NOW)
    assert view.state == "unknown"
    assert view.price is None
    assert view.transport_alive is True
    assert authorize_feed_use(view)["accepted"] is False


def test_stale_or_conflicting_snapshot_does_not_clear_a_gap():
    stream = gate()
    stream.ingest(**payload(sequence=2))
    with pytest.raises(FeedRejected, match="sequence gap"):
        stream.ingest(**payload(sequence=5))
    with pytest.raises(FeedRejected, match="stale observation"):
        stream.resync(**payload(sequence=9, event_time=NOW - timedelta(seconds=10)))
    assert stream.gap_open("licensed", "BTC-USD") is True
    with pytest.raises(FeedRejected, match="snapshot behind cursor"):
        stream.resync(**payload(sequence=1, event_time=NOW - timedelta(seconds=2)))
    assert stream.gap_open("licensed", "BTC-USD") is True
    assert stream.status("licensed", "BTC-USD", NOW).price is None


def test_feed_status_rejects_a_live_order_flag():
    with pytest.raises(RuntimeError, match="live_orders_forbidden"):
        FeedStatus(
            state="fresh",
            usable=True,
            sequence=1,
            price=Decimal("1"),
            event_time=NOW,
            transport_alive=True,
            live_orders_enabled=True,
        )
