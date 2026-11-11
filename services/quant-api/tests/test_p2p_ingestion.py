"""Tests for authorized P2P market-data ingestion (issue #87)."""
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.p2p_ingestion import (
    DATASET,
    ENABLE_ENV,
    PAPER_INTENT_ENV,
    IngestionRejection,
    ingest_p2p_observation,
    paper_intent_from_observation,
)
from osiris_api.risk import RiskLimits

NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)
PROVIDERS = {"p2p-research-node-a", "p2p-research-node-b"}


def _observation(**overrides):
    payload = overrides.pop("payload", "mid=101.25;spread=0.05")
    obs = {
        "schema": "osiris.observation.v1",
        "instrument": "BTC-USD",
        "observed_at": (NOW - timedelta(seconds=30)).isoformat(),
        "provider": "p2p-research-node-a",
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
    }
    obs.update(overrides)
    return obs


@pytest.fixture
def store(tmp_path):
    # ResearchStore opens a fresh SQLite connection per operation, so tests
    # need a file-backed database rather than per-connection ":memory:".
    return ResearchStore(str(tmp_path / "research.sqlite3"))


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")


def test_ingest_happy_path(store, enabled):
    result = ingest_p2p_observation(
        store, _observation(), allowed_providers=PROVIDERS, now=NOW
    )
    assert result["ingested"] is True
    assert result["live_execution"] is False
    rows = store.list(DATASET)
    assert len(rows) == 1
    assert rows[0]["payload"]["provider"] == "p2p-research-node-a"


def test_kill_switch_blocks_when_unset(store, monkeypatch):
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    with pytest.raises(IngestionRejection, match="kill_switch"):
        ingest_p2p_observation(
            store, _observation(), allowed_providers=PROVIDERS, now=NOW
        )


def test_empty_provider_allowlist_fails_closed(store, enabled):
    with pytest.raises(IngestionRejection, match="no_authorized_providers"):
        ingest_p2p_observation(store, _observation(), allowed_providers=[], now=NOW)


def test_unauthorized_provider_rejected(store, enabled):
    with pytest.raises(IngestionRejection, match="provider_not_authorized"):
        ingest_p2p_observation(
            store, _observation(provider="rogue-node"),
            allowed_providers=PROVIDERS, now=NOW,
        )


def test_stale_observation_rejected(store, enabled):
    stale = _observation(observed_at=(NOW - timedelta(seconds=3600)).isoformat())
    with pytest.raises(IngestionRejection, match="observation_stale"):
        ingest_p2p_observation(store, stale, allowed_providers=PROVIDERS, now=NOW)


def test_future_dated_observation_rejected(store, enabled):
    future = _observation(observed_at=(NOW + timedelta(seconds=300)).isoformat())
    with pytest.raises(IngestionRejection, match="future_dated"):
        ingest_p2p_observation(store, future, allowed_providers=PROVIDERS, now=NOW)


def test_minor_future_skew_clamped_and_accepted(store, enabled):
    slight = _observation(observed_at=(NOW + timedelta(seconds=5)).isoformat())
    result = ingest_p2p_observation(
        store, slight, allowed_providers=PROVIDERS, now=NOW
    )
    assert result["ingested"] is True


def test_payload_hash_mismatch_rejected(store, enabled):
    obs = _observation()
    obs["payload_sha256"] = "0" * 64
    with pytest.raises(IngestionRejection, match="payload_hash_mismatch"):
        ingest_p2p_observation(store, obs, allowed_providers=PROVIDERS, now=NOW)


def test_unknown_schema_rejected(store, enabled):
    with pytest.raises(IngestionRejection, match="unsupported_schema"):
        ingest_p2p_observation(
            store, _observation(schema="osiris.observation.v0"),
            allowed_providers=PROVIDERS, now=NOW,
        )


def test_missing_timezone_rejected(store, enabled):
    naive = _observation(observed_at="2026-11-06T11:59:30")
    with pytest.raises(IngestionRejection, match="observed_at"):
        ingest_p2p_observation(store, naive, allowed_providers=PROVIDERS, now=NOW)


def test_duplicate_observation_fails_closed(store, enabled):
    ingest_p2p_observation(store, _observation(), allowed_providers=PROVIDERS, now=NOW)
    with pytest.raises(IngestionRejection, match="store_rejected"):
        ingest_p2p_observation(
            store, _observation(), allowed_providers=PROVIDERS, now=NOW
        )


def test_oversized_payload_rejected(store, enabled):
    big = "x" * 20000
    obs = _observation(payload=big)
    with pytest.raises(IngestionRejection, match="payload_too_large"):
        ingest_p2p_observation(store, obs, allowed_providers=PROVIDERS, now=NOW)


def test_paper_intent_blocked_without_second_opt_in(store, enabled, monkeypatch):
    monkeypatch.delenv(PAPER_INTENT_ENV, raising=False)
    record = ingest_p2p_observation(
        store, _observation(), allowed_providers=PROVIDERS, now=NOW
    )
    intent = paper_intent_from_observation(record, quantity=1, reference_price=100)
    assert intent["paper_order_intent"]["status"] == "BLOCKED_BY_KILL_SWITCH"
    assert intent["execution"]["live_execution"] is False
    assert intent["execution"]["broker_order_submitted"] is False


def test_paper_intent_approved_within_risk_limits(store, enabled, monkeypatch):
    monkeypatch.setenv(PAPER_INTENT_ENV, "1")
    record = ingest_p2p_observation(
        store, _observation(), allowed_providers=PROVIDERS, now=NOW
    )
    intent = paper_intent_from_observation(record, quantity=1, reference_price=100)
    assert intent["paper_order_intent"]["status"] == "PAPER_INTENT_APPROVED"
    assert intent["risk"]["approved_for_paper"] is True
    assert intent["execution"]["live_execution"] is False


def test_paper_intent_blocked_by_risk_gate(store, enabled, monkeypatch):
    monkeypatch.setenv(PAPER_INTENT_ENV, "1")
    record = ingest_p2p_observation(
        store, _observation(), allowed_providers=PROVIDERS, now=NOW
    )
    intent = paper_intent_from_observation(
        record, quantity=1000, reference_price=100,
        limits=RiskLimits(max_order_notional=1000.0),
    )
    assert intent["paper_order_intent"]["status"] == "BLOCKED_BY_RISK"
    assert intent["risk"]["reason"] == "max_order_notional_exceeded"
    assert intent["execution"]["live_execution"] is False


@pytest.mark.parametrize("field", ["max_age_seconds", "max_future_skew_seconds"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "300"])
def test_invalid_freshness_configuration_never_stores(store, enabled, field, value):
    with pytest.raises(IngestionRejection, match="invalid_freshness_bounds"):
        ingest_p2p_observation(
            store, _observation(), allowed_providers=PROVIDERS, now=NOW, **{field: value},
        )
    assert store.list(DATASET) == []


def test_ingestion_requires_an_explicit_timezone_on_injected_clock(store, enabled):
    with pytest.raises(IngestionRejection, match="ingestion_time_missing_timezone"):
        ingest_p2p_observation(
            store, _observation(), allowed_providers=PROVIDERS, now=NOW.replace(tzinfo=None),
        )
    assert store.list(DATASET) == []
