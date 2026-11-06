"""Pending retries must pass the policy in force when delivery is attempted."""
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.observation_outbox import ObservationOutbox
from osiris_api.p2p_ingestion import (
    DATASET,
    ENABLE_ENV as INGEST_ENV,
    ingest_p2p_observation,
)
from osiris_api.unified_observation_bridge import (
    ENABLE_ENV,
    UnifiedBridgeRefused,
    broadcast_unified_cycle,
)

NOW = datetime(2026, 11, 6, 4, tzinfo=timezone.utc)


def observation(instrument="BTCUSDT", *, observed_at=NOW):
    payload = '{"value":42}'
    return {
        "schema": "osiris.observation.v1",
        "provider": "binance-public",
        "instrument": instrument,
        "observed_at": observed_at.isoformat(),
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
    }


def market_sources(observations=(), *, providers=("binance-public",)):
    return {
        "market": (
            lambda **kwargs: {"observations": list(observations), "errors": []},
            frozenset(providers),
        ),
    }


@pytest.fixture
def pending_retry(monkeypatch, tmp_path):
    """Reopen a genuine pending delivery after the local node's pipe broke."""
    monkeypatch.setenv(ENABLE_ENV, "1")
    monkeypatch.setenv(INGEST_ENV, "1")
    store_path = str(tmp_path / "research.sqlite3")
    outbox_path = store_path
    store = ResearchStore(store_path)
    outbox = ObservationOutbox(outbox_path)

    def disconnected(_line):
        raise BrokenPipeError("node disconnected")

    with pytest.raises(BrokenPipeError):
        broadcast_unified_cycle(
            disconnected,
            store=store,
            collectors=market_sources([observation()]),
            outbox=outbox,
            now=NOW,
        )
    assert len(store.list(DATASET)) == 1
    assert outbox.pending_count() == 1
    return ResearchStore(store_path), ObservationOutbox(outbox_path)


def test_healthy_retry_after_restart_does_not_reinsert_or_rebroadcast(pending_retry):
    store, outbox = pending_retry
    lines = []
    result = broadcast_unified_cycle(
        lines.append,
        store=store,
        collectors=market_sources([observation()]),
        outbox=outbox,
        now=NOW,
    )
    assert result["broadcast"] == 1
    assert result["ingested"] == 0
    assert result["duplicates_skipped"] == 1
    assert result["outbox_rejected"] == 0
    assert json.loads(lines[0]) == observation()
    assert outbox.pending_count() == 0
    assert len(store.list(DATASET)) == 1

    repeated = broadcast_unified_cycle(
        lines.append,
        store=store,
        collectors=market_sources([observation()]),
        outbox=outbox,
        now=NOW,
    )
    assert repeated["broadcast"] == 0
    assert len(lines) == 1


def test_disabled_ingestion_preserves_pending_until_reenabled(monkeypatch, pending_retry):
    store, outbox = pending_retry
    lines = []
    monkeypatch.delenv(INGEST_ENV)
    stopped = broadcast_unified_cycle(
        lines.append,
        store=store,
        collectors=market_sources([observation()]),
        outbox=outbox,
        now=NOW,
    )
    assert stopped["broadcast"] == 0
    assert stopped["rejected"] == 1
    assert stopped["outbox_rejected"] == 0
    assert outbox.pending_count() == 1
    assert outbox.rejected_count() == 0
    assert lines == []

    monkeypatch.setenv(INGEST_ENV, "1")
    resumed = broadcast_unified_cycle(
        lines.append,
        store=store,
        collectors=market_sources(),
        outbox=outbox,
        now=NOW,
    )
    assert resumed["broadcast"] == 1
    assert outbox.pending_count() == 0
    assert len(store.list(DATASET)) == 1


def test_disabled_bridge_does_not_touch_pending(monkeypatch, pending_retry):
    store, outbox = pending_retry
    monkeypatch.delenv(ENABLE_ENV)
    lines = []
    with pytest.raises(UnifiedBridgeRefused, match="unified_broadcast_disabled"):
        broadcast_unified_cycle(
            lines.append, store=store, collectors=market_sources(), outbox=outbox, now=NOW,
        )
    assert lines == []
    assert outbox.pending_count() == 1
    assert outbox.rejected_count() == 0


def test_mid_drain_disable_retains_remaining_rows_and_reports_delivered_count(
    monkeypatch, pending_retry,
):
    store, outbox = pending_retry
    second = observation("ETHUSDT")
    ingest_p2p_observation(
        store, second, allowed_providers={"binance-public"}, now=NOW, outbox=outbox,
    )
    assert outbox.pending_count() == 2
    lines = []

    def stop_after_first(line):
        lines.append(line)
        monkeypatch.setenv(INGEST_ENV, "0")

    paused = broadcast_unified_cycle(
        stop_after_first,
        store=store,
        collectors=market_sources(),
        outbox=outbox,
        now=NOW,
    )
    assert paused["broadcast"] == 1
    assert paused["outbox_rejected"] == 0
    assert [json.loads(line)["instrument"] for line in lines] == ["BTCUSDT"]
    assert outbox.pending_count() == 1
    assert outbox.rejected_count() == 0

    monkeypatch.setenv(INGEST_ENV, "1")
    resumed = broadcast_unified_cycle(
        lines.append,
        store=store,
        collectors=market_sources(),
        outbox=ObservationOutbox(outbox.path),
        now=NOW + timedelta(seconds=1),
    )
    assert resumed["broadcast"] == 1
    assert resumed["outbox_rejected"] == 0
    assert [json.loads(line)["instrument"] for line in lines] == ["BTCUSDT", "ETHUSDT"]
    assert outbox.pending_count() == 0
    assert outbox.rejected_count() == 0
    assert len(store.list(DATASET)) == 2


@pytest.mark.parametrize("retry_time", [NOW + timedelta(seconds=301), NOW - timedelta(seconds=61)])
def test_retry_freshness_is_checked_and_bad_row_does_not_block_fresh_data(
    pending_retry, retry_time,
):
    store, outbox = pending_retry
    fresh = observation("FRESH", observed_at=retry_time)
    lines = []
    result = broadcast_unified_cycle(
        lines.append,
        store=store,
        collectors=market_sources([fresh]),
        outbox=outbox,
        now=retry_time,
    )
    assert result["broadcast"] == 1
    assert result["ingested"] == 1
    assert result["rejected"] == 0
    assert result["outbox_rejected"] == 1
    assert [json.loads(line)["instrument"] for line in lines] == ["FRESH"]
    assert outbox.pending_count() == 0
    assert ObservationOutbox(outbox.path).rejected_count() == 1


@pytest.mark.parametrize(
    "collectors",
    [
        market_sources(providers=("coinbase-public",)),
        {"world": (lambda **kwargs: {"observations": [], "errors": []},
                   frozenset({"usgs-earthquakes"}))},
    ],
    ids=["provider-removed", "source-removed"],
)
def test_current_provider_and_source_authorization_applies_to_pending(pending_retry, collectors):
    store, outbox = pending_retry
    lines = []
    result = broadcast_unified_cycle(
        lines.append, store=store, collectors=collectors, outbox=outbox, now=NOW,
    )
    assert result["broadcast"] == 0
    assert result["outbox_rejected"] == 1
    assert result["rejected"] == 0
    assert lines == []
    assert outbox.pending_count() == 0
    assert outbox.rejected_count() == 1
    assert len(store.list(DATASET)) == 1


@pytest.mark.parametrize("corruption", ["invalid-json", "hash-mismatch"])
def test_corrupt_persisted_observation_is_quarantined_before_writer(pending_retry, corruption):
    store, outbox = pending_retry
    modified = observation()
    modified["payload"] = '{"value":999}'
    encoded = "{" if corruption == "invalid-json" else json.dumps(modified)
    with sqlite3.connect(outbox.path) as connection:
        connection.execute(
            "UPDATE pending_observations SET observation_json=?", (encoded,),
        )

    fresh = observation("AFTER_CORRUPT")
    lines = []
    result = broadcast_unified_cycle(
        lines.append,
        store=store,
        collectors=market_sources([fresh]),
        outbox=ObservationOutbox(outbox.path),
        now=NOW,
    )
    assert result["broadcast"] == 1
    assert result["outbox_rejected"] == 1
    assert result["rejected"] == 0
    assert [json.loads(line)["instrument"] for line in lines] == ["AFTER_CORRUPT"]
    assert outbox.rejected_count() == 1
    assert outbox.pending_count() == 0


def test_invalid_source_preflight_preserves_pending_without_partial_output(pending_retry):
    store, outbox = pending_retry
    collectors = market_sources()
    collectors["unknown"] = (
        lambda **kwargs: {"observations": [], "errors": []}, frozenset({"binance-public"}),
    )
    lines = []
    with pytest.raises(UnifiedBridgeRefused, match="unknown_source"):
        broadcast_unified_cycle(
            lines.append, store=store, collectors=collectors, outbox=outbox, now=NOW,
        )
    assert lines == []
    assert outbox.pending_count() == 1
    assert outbox.rejected_count() == 0


def test_no_active_sources_preserves_pending(pending_retry):
    store, outbox = pending_retry
    lines = []
    result = broadcast_unified_cycle(
        lines.append, store=store, collectors={}, outbox=outbox, now=NOW,
    )
    assert result["broadcast"] == 0
    assert result["outbox_rejected"] == 0
    assert lines == []
    assert outbox.pending_count() == 1
    assert outbox.rejected_count() == 0
