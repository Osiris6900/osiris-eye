"""Deterministic fail-closed market feed quality tests."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from osiris_api.feed_quality_gate import FeedObservation, validate_observation


NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


def sample():
    return FeedObservation(
        source="licensed-vendor", instrument="ABC", event_time=NOW - timedelta(seconds=2),
        available_time=NOW - timedelta(seconds=1), price=100.0,
        entitled=True, authenticated=True,
    )


def test_accepts_fresh_authorized_observation():
    assert validate_observation(sample(), now=NOW).accepted


def test_rejects_unentitled_and_unauthenticated():
    assert validate_observation(replace(sample(), entitled=False), now=NOW).reason == "not_entitled"
    assert validate_observation(replace(sample(), authenticated=False), now=NOW).reason == "unauthenticated"


def test_rejects_stale_future_and_out_of_order():
    assert validate_observation(replace(sample(), event_time=NOW-timedelta(seconds=9), available_time=NOW-timedelta(seconds=8)), now=NOW).reason == "stale"
    assert validate_observation(replace(sample(), available_time=NOW+timedelta(seconds=1)), now=NOW).reason == "future_timestamp"
    assert validate_observation(sample(), now=NOW, previous_event_time=sample().event_time).reason == "out_of_order"


def test_rejects_invalid_price_and_naive_time():
    assert validate_observation(replace(sample(), price=float("nan")), now=NOW).reason == "invalid_price"
    assert validate_observation(replace(sample(), event_time=NOW.replace(tzinfo=None)), now=NOW).reason == "timezone_required"
