"""Security tests for unified observation bridge."""
import hashlib
import json
from datetime import datetime, timezone

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.unified_observation_bridge import (
    ENABLE_ENV, UnifiedBridgeRefused, broadcast_unified_cycle,
)
from osiris_api.p2p_ingestion import ENABLE_ENV as INGEST_ENV

NOW = datetime(2026, 11, 6, tzinfo=timezone.utc)


def observation(provider, instrument):
    payload = json.dumps({"value": 42}, sort_keys=True)
    return {
        "schema": "osiris.observation.v1", "provider": provider,
        "instrument": instrument, "observed_at": NOW.isoformat(),
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
    }


def sources():
    return {
        "market": (lambda **kw: {"observations": [observation("binance-public", "BTCUSDT")], "errors": []}, frozenset({"binance-public"})),
        "world": (lambda **kw: {"observations": [observation("usgs-earthquakes", "QUAKE")], "errors": []}, frozenset({"usgs-earthquakes"})),
        "celestial": (lambda **kw: {"observations": [observation("astro-computed", "MOON")], "errors": []}, frozenset({"astro-computed"})),
    }


@pytest.fixture
def store(tmp_path):
    return ResearchStore(str(tmp_path / "unified.sqlite3"))


def test_three_sources_pass_gates_and_duplicates_do_not_rebroadcast(monkeypatch, store):
    monkeypatch.setenv(ENABLE_ENV, "1")
    monkeypatch.setenv(INGEST_ENV, "1")
    lines = []
    first = broadcast_unified_cycle(lines.append, store=store, collectors=sources(), now=NOW)
    second = broadcast_unified_cycle(lines.append, store=store, collectors=sources(), now=NOW)
    assert first["broadcast"] == 3
    assert second["broadcast"] == 0
    assert second["duplicates_skipped"] == 3
    assert len(lines) == 3


def test_both_kill_switches_fail_closed(monkeypatch, store):
    lines = []
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    with pytest.raises(UnifiedBridgeRefused, match="unified_broadcast_disabled"):
        broadcast_unified_cycle(lines.append, store=store, collectors=sources(), now=NOW)
    monkeypatch.setenv(ENABLE_ENV, "1")
    monkeypatch.delenv(INGEST_ENV, raising=False)
    result = broadcast_unified_cycle(lines.append, store=store, collectors=sources(), now=NOW)
    assert result["broadcast"] == 0
    assert result["rejected"] == 3
    assert lines == []


def test_unapproved_provider_never_broadcasts(monkeypatch, store):
    monkeypatch.setenv(ENABLE_ENV, "1")
    monkeypatch.setenv(INGEST_ENV, "1")
    lines = []
    source = {"market": (lambda **kw: {"observations": [observation("untrusted", "BTCUSDT")], "errors": []}, frozenset({"binance-public"}))}
    result = broadcast_unified_cycle(lines.append, store=store, collectors=source, now=NOW)
    assert result["rejected"] == 1
    assert lines == []


def test_missing_store_refused(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    with pytest.raises(UnifiedBridgeRefused, match="validated_store_required"):
        broadcast_unified_cycle(lambda line: None, store=None, collectors=sources(), now=NOW)


def test_tampered_hash_stale_and_future_data_rejected(monkeypatch, store):
    from datetime import timedelta
    monkeypatch.setenv(ENABLE_ENV, "1")
    monkeypatch.setenv(INGEST_ENV, "1")
    tampered = observation("binance-public", "TAMPERED")
    tampered["payload"] = '{"value":999}'
    stale = observation("binance-public", "STALE")
    stale["observed_at"] = (NOW - timedelta(minutes=6)).isoformat()
    future = observation("binance-public", "FUTURE")
    future["observed_at"] = (NOW + timedelta(minutes=2)).isoformat()
    good = observation("binance-public", "GOOD")
    src = {"market": (lambda **kw: {
        "observations": [tampered, stale, future, good], "errors": []
    }, frozenset({"binance-public"}))}
    lines = []
    result = broadcast_unified_cycle(lines.append, store=store, collectors=src, now=NOW)
    assert result["rejected"] == 3
    assert result["broadcast"] == 1
    assert json.loads(lines[0])["instrument"] == "GOOD"


def test_unknown_source_preflight_prevents_partial_broadcast(monkeypatch, store):
    monkeypatch.setenv(ENABLE_ENV, "1")
    monkeypatch.setenv(INGEST_ENV, "1")
    src = sources()
    src["unknown"] = (lambda **kw: {"observations": [], "errors": []}, frozenset({"binance-public"}))
    lines = []
    with pytest.raises(UnifiedBridgeRefused, match="unknown_source"):
        broadcast_unified_cycle(lines.append, store=store, collectors=src, now=NOW)
    assert lines == []


def test_source_failure_isolated_from_other_sources(monkeypatch, store):
    monkeypatch.setenv(ENABLE_ENV, "1")
    monkeypatch.setenv(INGEST_ENV, "1")
    src = sources()
    def unavailable(**kwargs):
        raise RuntimeError("offline")
    src["world"] = (unavailable, frozenset({"usgs-earthquakes"}))
    lines = []
    result = broadcast_unified_cycle(lines.append, store=store, collectors=src, now=NOW)
    assert result["broadcast"] == 2
    assert len(result["source_errors"]) == 1
    assert result["source_errors"][0]["source"] == "world"


def test_unapproved_configuration_preflight_prevents_partial_output(monkeypatch, store):
    monkeypatch.setenv(ENABLE_ENV, "1")
    monkeypatch.setenv(INGEST_ENV, "1")
    src = sources()
    src["celestial"] = (src["celestial"][0], frozenset({"untrusted"}))
    lines = []
    with pytest.raises(UnifiedBridgeRefused, match="no_authorized_providers"):
        broadcast_unified_cycle(lines.append, store=store, collectors=src, now=NOW)
    assert lines == []
