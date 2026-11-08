"""Negative admission tests for market data provenance and freshness (#178)."""
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from osiris_api.p2p_ingestion import (
    ENABLE_ENV, IngestionRejection, validate_p2p_observation,
)

NOW = datetime(2026, 11, 8, 6, 0, tzinfo=timezone.utc)


def observation(**changes):
    payload = '{"last":101.25}'
    data = {
        "schema": "osiris.observation.v1", "instrument": "BTC-USD",
        "provider": "coinbase-public", "observed_at": NOW.isoformat(),
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
    }
    data.update(changes)
    return data


def test_authorized_fresh_observation_passes(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    result = validate_p2p_observation(
        observation(), allowed_providers={"coinbase-public"}, now=NOW,
    )
    assert result["provider"] == "coinbase-public"


@pytest.mark.parametrize(("changes", "reason"), [
    ({"provider": "unknown-feed"}, "provider_not_authorized"),
    ({"observed_at": (NOW - timedelta(seconds=301)).isoformat()}, "observation_stale"),
    ({"observed_at": (NOW + timedelta(seconds=61)).isoformat()}, "observation_future_dated"),
    ({"payload": '{"last":999}'}, "payload_hash_mismatch"),
    ({"schema": "osiris.observation.v0"}, "unsupported_schema"),
    ({"observed_at": "2026-11-08T06:00:00"}, "observed_at_missing_timezone"),
])
def test_bad_market_observation_fails_closed(monkeypatch, changes, reason):
    monkeypatch.setenv(ENABLE_ENV, "1")
    with pytest.raises(IngestionRejection, match=reason):
        validate_p2p_observation(
            observation(**changes), allowed_providers={"coinbase-public"}, now=NOW,
        )


def test_disabled_ingestion_rejects_even_valid_data(monkeypatch):
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    with pytest.raises(IngestionRejection, match="ingestion_disabled_kill_switch"):
        validate_p2p_observation(
            observation(), allowed_providers={"coinbase-public"}, now=NOW,
        )
