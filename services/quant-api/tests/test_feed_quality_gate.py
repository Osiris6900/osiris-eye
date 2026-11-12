"""Deterministic fail-closed market feed quality tests."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from osiris_api.feed_quality_gate import FeedObservation, validate_observation

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


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


@pytest.mark.parametrize("flag", [1, "true", "false", [True], {"approved": True}, None])
@pytest.mark.parametrize("field,reason", [
    ("entitled", "not_entitled"), ("authenticated", "unauthenticated"),
])
def test_truthy_metadata_cannot_substitute_for_explicit_authorization(field, reason, flag):
    decision = validate_observation(replace(sample(), **{field: flag}), now=NOW)
    assert decision == type(decision)(False, reason)


@pytest.mark.parametrize("field", ["source", "instrument"])
@pytest.mark.parametrize("value", [None, 1, True, [], {}, "", "  "])
def test_malformed_identity_returns_a_refusal_instead_of_crashing(field, value):
    decision = validate_observation(replace(sample(), **{field: value}), now=NOW)
    assert not decision.accepted
    assert decision.reason == "invalid_identity"


@pytest.mark.parametrize("value", [True, False, "5", None, [], float("nan"),
                                      float("inf"), 10**1000, 0, -1])
def test_invalid_freshness_policy_cannot_approve_an_observation(value):
    decision = validate_observation(sample(), now=NOW, max_age_seconds=value)
    assert not decision.accepted
    assert decision.reason == "invalid_max_age"


def test_extremely_large_price_fails_closed_without_numeric_overflow():
    decision = validate_observation(replace(sample(), price=10**1000), now=NOW)
    assert not decision.accepted
    assert decision.reason == "invalid_price"


def test_delayed_old_event_cannot_be_made_fresh_by_a_new_arrival_timestamp():
    observation = replace(sample(), event_time=NOW-timedelta(hours=1), available_time=NOW)
    decision = validate_observation(observation, now=NOW)
    assert not decision.accepted
    assert decision.reason == "stale"


def test_event_age_boundary_uses_actual_instants_across_timezones():
    at_boundary = replace(sample(), event_time=NOW-timedelta(seconds=5), available_time=NOW)
    assert validate_observation(at_boundary, now=NOW).accepted
    zone = timezone(timedelta(hours=5, minutes=30))
    same_instant = replace(at_boundary, event_time=at_boundary.event_time.astimezone(zone))
    assert validate_observation(same_instant, now=NOW).accepted
    too_old = replace(at_boundary, event_time=at_boundary.event_time-timedelta(microseconds=1))
    assert validate_observation(too_old, now=NOW).reason == "stale"


def test_invalid_observation_container_fails_closed():
    assert validate_observation({}, now=NOW).reason == "invalid_observation"
