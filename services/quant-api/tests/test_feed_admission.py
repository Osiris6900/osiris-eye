"""Operator-attested feed admission is explicit, bounded, and fail-closed."""
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.feed_admission import FeedAdmissionRejection, admit_feed
from osiris_api.p2p_ingestion import (
    DATASET,
    ENABLE_ENV,
    IngestionRejection,
    ingest_p2p_observation,
)

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)
PROVIDERS = {"binance-public"}


def _policy(**overrides):
    record = {
        "authorized_source": "binance-spot-public-ticker",
        "entitlement_reference": "operator-review-2026-11-07",
        "redistribution_rights": "permitted",
        "availability_timestamps_required": True,
        "instruments": {"BTC-USD"},
        "quality_review": {
            "status": "passed",
            "reviewer": "independent-quality-reviewer",
            "reviewed_at": (NOW - timedelta(days=1)).isoformat(),
        },
    }
    record.update(overrides)
    return {"binance-public": record}


def _observation():
    payload = '{"last":101.25}'
    return {
        "schema": "osiris.observation.v1", "instrument": "BTC-USD",
        "observed_at": (NOW - timedelta(seconds=30)).isoformat(),
        "provider": "binance-public", "payload": payload,
        "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
    }


def test_valid_policy_returns_auditable_attestation_boundary():
    report = admit_feed(
        provider="binance-public", instrument="BTC-USD", policy=_policy(), now=NOW,
    )
    assert report["status"] == "admitted"
    assert report["redistribution_rights"] == "permitted"
    assert report["availability_timestamps"] == "event_time_and_available_at_required"
    assert report["verification_boundary"] == (
        "operator_attestation_not_independent_verification"
    )


def test_unknown_provider_policy_is_rejected():
    with pytest.raises(FeedAdmissionRejection, match="provider_policy_missing"):
        admit_feed(provider="unknown", instrument="BTC-USD", policy=_policy(), now=NOW)


@pytest.mark.parametrize("rights", ["unknown", "not_permitted", None])
def test_unclear_or_denied_redistribution_rights_fail_closed(rights):
    with pytest.raises(FeedAdmissionRejection, match="redistribution_not_permitted"):
        admit_feed(
            provider="binance-public", instrument="BTC-USD",
            policy=_policy(redistribution_rights=rights), now=NOW,
        )


def test_instrument_must_be_explicitly_covered():
    with pytest.raises(FeedAdmissionRejection, match="instrument_not_authorized"):
        admit_feed(
            provider="binance-public", instrument="ETH-USD", policy=_policy(), now=NOW,
        )


def test_stale_quality_review_is_rejected():
    stale = (NOW - timedelta(days=31)).isoformat()
    policy = _policy()
    policy["binance-public"]["quality_review"]["reviewed_at"] = stale
    with pytest.raises(FeedAdmissionRejection, match="quality_review_stale"):
        admit_feed(
            provider="binance-public", instrument="BTC-USD", policy=policy, now=NOW,
        )


def test_quality_reviewer_and_pass_status_are_required():
    policy = _policy()
    policy["binance-public"]["quality_review"]["reviewer"] = ""
    with pytest.raises(FeedAdmissionRejection, match="quality_reviewer_required"):
        admit_feed(
            provider="binance-public", instrument="BTC-USD", policy=policy, now=NOW,
        )


def test_ingestion_persists_feed_admission_with_observation(tmp_path, monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    store = ResearchStore(str(tmp_path / "research.sqlite3"))
    result = ingest_p2p_observation(
        store, _observation(), allowed_providers=PROVIDERS, now=NOW,
        feed_policy=_policy(), require_feed_policy=True,
    )
    assert result["feed_admission"]["status"] == "admitted"
    stored = store.get(DATASET, result["source"], result["record_key"])
    assert stored["payload"]["feed_admission"]["authorized_source"] == (
        "binance-spot-public-ticker"
    )


def test_required_policy_missing_rejects_before_storage(tmp_path, monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    store = ResearchStore(str(tmp_path / "research.sqlite3"))
    with pytest.raises(IngestionRejection, match="feed_policy_required"):
        ingest_p2p_observation(
            store, _observation(), allowed_providers=PROVIDERS, now=NOW,
            require_feed_policy=True,
        )
    assert store.list(DATASET) == []


def test_invalid_policy_rejects_before_storage(tmp_path, monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    store = ResearchStore(str(tmp_path / "research.sqlite3"))
    with pytest.raises(IngestionRejection, match="feed_admission_rejected"):
        ingest_p2p_observation(
            store, _observation(), allowed_providers=PROVIDERS, now=NOW,
            feed_policy=_policy(redistribution_rights="unknown"),
        )
    assert store.list(DATASET) == []


@pytest.mark.parametrize("bad_provider", [None, [], 123, "", "  "])
def test_feed_admission_rejects_invalid_provider(bad_provider):
    with pytest.raises(FeedAdmissionRejection, match="feed_provider_required"):
        admit_feed(provider=bad_provider, instrument="BTC-USD", policy=_policy(), now=NOW)


@pytest.mark.parametrize("bad_instrument", [None, [], 123, "", "  "])
def test_feed_admission_rejects_invalid_instrument(bad_instrument):
    with pytest.raises(FeedAdmissionRejection, match="feed_instrument_required"):
        admit_feed(provider="binance-public", instrument=bad_instrument, policy=_policy(), now=NOW)


@pytest.mark.parametrize("bad_age", [float("nan"), float("inf"), float("-inf")])
def test_feed_admission_rejects_nonfinite_review_age(bad_age):
    with pytest.raises(FeedAdmissionRejection, match="feed_quality_review_age_bound_invalid"):
        admit_feed(provider="binance-public", instrument="BTC-USD", policy=_policy(), now=NOW, max_review_age_seconds=bad_age)
