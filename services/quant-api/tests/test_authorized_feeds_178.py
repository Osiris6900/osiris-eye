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
