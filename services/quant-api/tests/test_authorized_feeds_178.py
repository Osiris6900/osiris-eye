"""Authorized adapter registry, entitlement binding and tick gate tests (#178)."""
from datetime import datetime, timedelta, timezone

import pytest

from osiris_api.authorized_feeds import (
    ADAPTER_ENTITLEMENTS,
    FEED_TICK_SCHEMA,
    FeedAdapterRejection,
    SyntheticAuthorizedAdapter,
    ingest_adapter_ticks,
    validate_feed_tick,
)

NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
START = NOW - timedelta(hours=2)


def _adapter(count=16, inject=()):
    return SyntheticAuthorizedAdapter(
        seed="research-178", symbols=["BTC-USD"], start=START,
        step_seconds=60, count=count, inject=inject,
    )


def test_valid_synthetic_batch_admitted():
    result = ingest_adapter_ticks(_adapter(), instruments=["BTC-USD"], now=NOW)
    assert result["status"] == "admitted"
    assert result["counts"] == {"fetched": 16, "admitted": 16, "rejected": 0}
    assert result["license_class"] == "synthetic_test_data"
    admission = result["admissions"]["BTC-USD"]
    assert admission["status"] == "admitted"
    assert admission["entitlement_reference"] == "synthetic-fixture-no-vendor-data"
    first = result["admitted"][0]
    assert first["schema"] == FEED_TICK_SCHEMA
    assert first["event_time"] < first["available_time"] < first["received_time"]


def test_synthetic_adapter_is_deterministic():
    left = _adapter().fetch(now=NOW)
    right = _adapter().fetch(now=NOW)
    assert left == right


def test_unregistered_adapter_refused_before_fetch():
    class SpyAdapter:
        name = "unregistered-vendor"
        called = False

        def fetch(self, *, now):
            self.called = True
            return []

    spy = SpyAdapter()
    with pytest.raises(FeedAdapterRejection, match="adapter_not_registered"):
        ingest_adapter_ticks(spy, instruments=["BTC-USD"], now=NOW)
    assert spy.called is False  # fail-closed before any data is read


def test_entitlement_not_permitted_refused(monkeypatch):
    record = dict(ADAPTER_ENTITLEMENTS["synthetic-authorized"])
    record["redistribution_rights"] = "prohibited"
    monkeypatch.setitem(ADAPTER_ENTITLEMENTS, "synthetic-authorized", record)
    with pytest.raises(
        FeedAdapterRejection,
        match="adapter_entitlement_rejected:feed_redistribution_not_permitted",
    ):
        ingest_adapter_ticks(_adapter(), instruments=["BTC-USD"], now=NOW)


def test_injected_bad_ticks_rejected_with_reasons():
    result = ingest_adapter_ticks(
        _adapter(inject={"delayed", "malformed", "stale", "future_available", "bad_price"}),
        instruments=["BTC-USD"], now=NOW,
    )
    assert result["counts"]["admitted"] == 11
    assert result["counts"]["rejected"] == 5
    reasons = sorted(item["reason"] for item in result["rejected"])
    assert reasons == [
        "tick_available_in_future",
        "tick_price_not_positive_finite",
        "tick_source_required",
        "tick_stale_beyond_delay",
        "tick_stale_beyond_delay",
    ]
    rejected_indexes = {item["index"] for item in result["rejected"]}
    assert rejected_indexes == {1, 2, 3, 4, 5}


def test_tick_outside_requested_instruments_rejected():
    result = ingest_adapter_ticks(_adapter(count=4), instruments=["ETH-USD"], now=NOW)
    assert result["counts"]["admitted"] == 0
    assert {item["reason"] for item in result["rejected"]} == {"tick_instrument_not_authorized"}
    assert result["status"] == "no_ticks_admitted"


def test_validate_tick_time_order_violated():
    tick = _adapter(count=1).fetch(now=NOW)[0]
    tick["available_time"] = "2026-11-08T09:00:00Z"  # before event_time
    with pytest.raises(FeedAdapterRejection, match="tick_time_order_violated"):
        validate_feed_tick(tick, now=NOW)


def test_validate_tick_rejects_naive_now():
    tick = _adapter(count=1).fetch(now=NOW)[0]
    with pytest.raises(FeedAdapterRejection, match="tick_now_missing_timezone"):
        validate_feed_tick(tick, now=NOW.replace(tzinfo=None))


def test_validate_tick_rejects_bad_delay_bound():
    tick = _adapter(count=1).fetch(now=NOW)[0]
    with pytest.raises(FeedAdapterRejection, match="tick_max_delay_bound_invalid"):
        validate_feed_tick(tick, now=NOW, max_delay_seconds=-1)


def test_validate_tick_rejects_non_object_and_schema():
    with pytest.raises(FeedAdapterRejection, match="tick_not_an_object"):
        validate_feed_tick("not-a-tick", now=NOW)
    tick = _adapter(count=1).fetch(now=NOW)[0]
    tick["schema"] = "osiris.feed_tick.v0"
    with pytest.raises(FeedAdapterRejection, match="tick_schema_unsupported"):
        validate_feed_tick(tick, now=NOW)


def test_tick_source_spoof_is_rejected_even_from_registered_adapter():
    class SpoofingRegisteredAdapter:
        name = "synthetic-authorized"

        def fetch(self, *, now):
            tick = _adapter(count=1).fetch(now=now)[0]
            tick["source"] = "unlicensed-source"
            return [tick]

    result = ingest_adapter_ticks(
        SpoofingRegisteredAdapter(), instruments=["BTC-USD"], now=NOW,
    )
    assert result["counts"] == {"fetched": 1, "admitted": 0, "rejected": 1}
    assert result["rejected"][0]["reason"] == "tick_source_mismatch"


def test_duplicate_tick_is_rejected_without_doubling_admission():
    class DuplicateRegisteredAdapter:
        name = "synthetic-authorized"

        def fetch(self, *, now):
            tick = _adapter(count=1).fetch(now=now)[0]
            return [tick, dict(tick)]

    result = ingest_adapter_ticks(
        DuplicateRegisteredAdapter(), instruments=["BTC-USD"], now=NOW,
    )
    assert result["counts"] == {"fetched": 2, "admitted": 1, "rejected": 1}
    assert result["rejected"] == [{"index": 1, "reason": "tick_duplicate"}]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("authorized_source", "different-source"),
        ("entitlement_reference", "self-attested-vendor"),
        ("redistribution_rights", "prohibited"),
        ("availability_timestamps_required", False),
        ("license_class", "licensed_vendor"),
        ("quality_review", {"status": "passed", "reviewer": "fake",
                            "reviewed_at": "2026-11-08T00:00:00Z"}),
    ],
)
def test_caller_cannot_override_reviewed_registry_entitlement(field, value):
    class Spy:
        name = "synthetic-authorized"
        called = False

        def fetch(self, *, now):
            self.called = True
            return []

    spy = Spy()
    policy = dict(ADAPTER_ENTITLEMENTS["synthetic-authorized"])
    policy["instruments"] = {"BTC-USD"}
    policy[field] = value
    with pytest.raises(
        FeedAdapterRejection, match="adapter_policy_registry_mismatch",
    ):
        ingest_adapter_ticks(
            spy, instruments=["BTC-USD"], now=NOW,
            policy={"synthetic-authorized": policy},
        )
    assert spy.called is False


def test_caller_policy_can_only_narrow_instrument_coverage():
    class Spy:
        name = "synthetic-authorized"
        called = False

        def fetch(self, *, now):
            self.called = True
            return []

    policy = dict(ADAPTER_ENTITLEMENTS["synthetic-authorized"])
    policy["instruments"] = {"ETH-USD"}
    spy = Spy()
    with pytest.raises(FeedAdapterRejection, match="adapter_policy_instruments_widened"):
        ingest_adapter_ticks(
            spy, instruments=["BTC-USD"], now=NOW,
            policy={"synthetic-authorized": policy},
        )
    assert spy.called is False


def test_untrusted_entitlement_policy_rejected_before_fetch():
    class Spy:
        name = "synthetic-authorized"
        called = False

        def fetch(self, *, now):
            self.called = True
            return []

    spy = Spy()
    with pytest.raises(FeedAdapterRejection, match="adapter_policy_invalid"):
        ingest_adapter_ticks(spy, instruments=["BTC-USD"], now=NOW, policy="unsafe")
    assert spy.called is False



def test_realtime_feed_rejects_expired_at_decision_but_preserves_history_mode():
    recent = SyntheticAuthorizedAdapter(
        seed="point-in-time-freshness", symbols=["BTC-USD"],
        start=NOW - timedelta(seconds=180), step_seconds=60, count=4,
    )
    historical = ingest_adapter_ticks(
        recent, instruments=["BTC-USD"], now=NOW,
    )
    assert historical["counts"] == {"fetched": 4, "admitted": 4, "rejected": 0}
    assert historical["decision_time_freshness_enforced"] is False

    current = ingest_adapter_ticks(
        recent, instruments=["BTC-USD"], now=NOW,
        realtime_max_age_seconds=120,
    )
    assert current["counts"] == {"fetched": 4, "admitted": 3, "rejected": 1}
    assert current["rejected"] == [{"index": 0, "reason": "tick_stale_at_decision"}]
    assert current["decision_time_freshness_enforced"] is True


@pytest.mark.parametrize("invalid", [0, -1, True, "300", float("nan"), float("inf")])
def test_invalid_realtime_age_is_rejected_not_silently_accepted(invalid):
    tick = _adapter(count=1).fetch(now=NOW)[0]
    with pytest.raises(FeedAdapterRejection, match="tick_decision_age_bound_invalid"):
        validate_feed_tick(tick, now=NOW, max_age_seconds_at_decision=invalid)


def test_future_received_timestamp_is_rejected_even_if_available_time_allowed():
    tick = _adapter(count=1).fetch(now=NOW)[0]
    tick["event_time"] = (NOW + timedelta(seconds=10)).isoformat()
    tick["available_time"] = (NOW + timedelta(seconds=30)).isoformat()
    tick["received_time"] = (NOW + timedelta(seconds=90)).isoformat()
    with pytest.raises(FeedAdapterRejection, match="tick_received_in_future"):
        validate_feed_tick(tick, now=NOW)


def test_realtime_expired_report_never_claims_license_attestation():
    batch = _adapter(count=1)
    report = ingest_adapter_ticks(
        batch, instruments=["BTC-USD"], now=NOW,
        realtime_max_age_seconds=300,
    )
    assert report["status"] == "no_ticks_admitted"
    assert report["rejected"] == [{"index": 0, "reason": "tick_stale_at_decision"}]
    assert report["verification_boundary"] == (
        "operator_attestation_and_synthetic_fixtures_not_vendor_verification"
    )
