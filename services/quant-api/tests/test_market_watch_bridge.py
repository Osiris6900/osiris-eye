"""Tests for the autonomous market-watch → P2P node bridge."""
import json

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.global_market_watch import (
    PROVIDER_BINANCE,
    PROVIDER_BYBIT,
    PROVIDER_FRANKFURTER,
    PROVIDER_STOOQ,
)
from osiris_api.market_watch_bridge import (
    ENABLE_ENV as BRIDGE_ENV,
)
from osiris_api.market_watch_bridge import (
    MAX_INTERVAL_SECONDS,
    MIN_INTERVAL_SECONDS,
    BridgeRefused,
    broadcast_cycle,
    run_bridge,
)
from osiris_api.p2p_ingestion import ENABLE_ENV as INGEST_ENV
from osiris_api.roaming_watch import RoamingTransport

BINANCE_TICKER = (
    b'{"symbol":"BTCUSDT","lastPrice":"69000.5",'
    b'"priceChangePercent":"1.2","volume":"100.0"}'
)
FRANKFURTER_LATEST = (
    b'{"base":"USD","date":"2026-11-06","rates":{"EUR":0.921}}'
)
STOOQ_QUOTES = (
    b"Symbol,Date,Time,Open,High,Low,Close,Volume\n"
    b"^SPX,2026-11-06,21:00:00,5800.0,5820.0,5790.0,5810.5,0\n"
)
BYBIT_TICKERS = json.dumps({
    "retCode": 0,
    "result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "69002.5", "volume24h": "1.0"}]},
}).encode()


def fake_transport(url: str, timeout: float) -> bytes:
    if "binance.com" in url:
        return BINANCE_TICKER
    if "frankfurter" in url:
        return FRANKFURTER_LATEST
    if "stooq.com" in url:
        return STOOQ_QUOTES
    raise RuntimeError("watch_source_unavailable")


WATCHLIST = {
    PROVIDER_BINANCE: ("BTCUSDT",),
    PROVIDER_FRANKFURTER: ("EUR",),
    PROVIDER_STOOQ: ("^spx",),
}


@pytest.fixture
def bridge_on(monkeypatch):
    monkeypatch.setenv(BRIDGE_ENV, "1")
    monkeypatch.setenv(INGEST_ENV, "1")


@pytest.fixture
def store(tmp_path):
    return ResearchStore(str(tmp_path / "bridge.sqlite3"))


def test_cycle_broadcasts_every_observation(bridge_on, store):
    lines: list[str] = []
    summary = broadcast_cycle(
        lines.append, store=store, watchlist=WATCHLIST, transport=fake_transport
    )
    assert summary["broadcast"] == 3
    assert summary["source_errors"] == []
    assert summary["coverage"]["sources_ok"] == 3
    assert summary["endpoint_stats"]["api.binance.com"]["successes"] == 1
    assert summary["live_execution"] is False
    parsed = [json.loads(line) for line in lines]
    assert {obs["instrument"] for obs in parsed} == {"BTCUSDT", "USD-EUR", "^SPX"}
    assert all(obs["schema"] == "osiris.observation.v1" for obs in parsed)


def test_cycle_refused_without_kill_switch(monkeypatch):
    monkeypatch.delenv(BRIDGE_ENV, raising=False)
    calls = []
    with pytest.raises(BridgeRefused, match="kill_switch"):
        broadcast_cycle(lambda line: None, watchlist=WATCHLIST,
                        transport=lambda url, timeout: calls.append(url))
    assert calls == []


def test_cycle_mirrors_into_store_through_gates(bridge_on, store, monkeypatch):
    monkeypatch.setenv(INGEST_ENV, "1")
    lines: list[str] = []
    first = broadcast_cycle(
        lines.append, store=store, watchlist=WATCHLIST, transport=fake_transport
    )
    second = broadcast_cycle(
        lines.append, store=store, watchlist=WATCHLIST, transport=fake_transport
    )
    assert first["ingested_local"] == 3
    assert second["ingested_local"] == 0
    assert second["duplicates_skipped"] == 3


def test_source_failure_isolated_others_broadcast(bridge_on, store):
    def broken(url: str, timeout: float) -> bytes:
        if "stooq.com" in url:
            raise RuntimeError("watch_source_unavailable")
        return fake_transport(url, timeout)

    lines: list[str] = []
    summary = broadcast_cycle(
        lines.append, store=store, watchlist=WATCHLIST, transport=broken
    )
    assert summary["broadcast"] == 2
    assert [e["source"] for e in summary["source_errors"]] == [PROVIDER_STOOQ]
    assert summary["coverage"]["failed_sources"] == [PROVIDER_STOOQ]
    assert summary["coverage"]["per_source"][PROVIDER_STOOQ]["status"] == "error"
    assert summary["endpoint_stats"]["stooq.com"]["failures"] == 1


def test_run_bridge_bounds_fail_closed(bridge_on, store):
    with pytest.raises(BridgeRefused, match="interval_out_of_bounds"):
        run_bridge(lambda line: None, store=store, interval_seconds=MIN_INTERVAL_SECONDS - 1)
    with pytest.raises(BridgeRefused, match="interval_out_of_bounds"):
        run_bridge(lambda line: None, store=store, interval_seconds=MAX_INTERVAL_SECONDS + 1)
    with pytest.raises(BridgeRefused, match="max_cycles_out_of_bounds"):
        run_bridge(lambda line: None, store=store, max_cycles=0)


def test_run_bridge_cycles_and_sleep(bridge_on, store):
    lines: list[str] = []
    sleeps: list[float] = []
    result = run_bridge(
        lines.append, store=store, watchlist=WATCHLIST, transport=fake_transport,
        interval_seconds=MIN_INTERVAL_SECONDS, max_cycles=3, sleep=sleeps.append,
    )
    assert result["cycles"] == 3
    assert len(lines) == 3
    assert sleeps == [MIN_INTERVAL_SECONDS] * 3
    assert [s["broadcast"] for s in result["summaries"]] == [3, 0, 0]

def test_broadcast_refuses_missing_store(bridge_on):
    lines = []
    with pytest.raises(BridgeRefused, match="validated_store_required"):
        broadcast_cycle(lines.append, watchlist=WATCHLIST, transport=fake_transport)
    assert lines == []


def test_ingestion_kill_switch_prevents_broadcast(bridge_on, store, monkeypatch):
    monkeypatch.delenv(INGEST_ENV, raising=False)
    lines = []
    result = broadcast_cycle(
        lines.append, store=store, watchlist=WATCHLIST, transport=fake_transport
    )
    assert result["broadcast"] == 0
    assert result["rejected_local"] == 3
    assert lines == []


def test_duplicate_observations_never_rebroadcast(bridge_on, store):
    lines = []
    first = broadcast_cycle(
        lines.append, store=store, watchlist=WATCHLIST, transport=fake_transport
    )
    second = broadcast_cycle(
        lines.append, store=store, watchlist=WATCHLIST, transport=fake_transport
    )
    assert first["broadcast"] == 3
    assert second["broadcast"] == 0
    assert second["duplicates_skipped"] == 3
    assert len(lines) == 3


def test_cycle_fails_over_to_official_alternate(bridge_on, store):
    calls = []

    def bybit(url, timeout):
        calls.append(url)
        if "api.bybit.com" in url:
            raise RuntimeError("watch_source_unavailable")
        return BYBIT_TICKERS

    lines = []
    summary = broadcast_cycle(
        lines.append, store=store, watchlist={PROVIDER_BYBIT: ("BTCUSDT",)},
        transport=bybit,
    )
    assert len(calls) == 2
    assert "api.bybit.com" in calls[0] and "api.bytick.com" in calls[1]
    assert summary["broadcast"] == 1
    assert json.loads(lines[0])["instrument"] == "BTCUSDT"
    assert summary["source_errors"] == []
    assert summary["coverage"]["sources_ok"] == 1
    assert summary["endpoint_stats"]["api.bybit.com"]["failures"] == 1
    assert summary["endpoint_stats"]["api.bytick.com"]["successes"] == 1


def test_run_bridge_reuses_roaming_state_across_cycles(bridge_on, store):
    calls = []

    def bybit(url, timeout):
        calls.append(url)
        if "api.bybit.com" in url:
            raise RuntimeError("watch_source_unavailable")
        return BYBIT_TICKERS

    result = run_bridge(
        lambda line: None, store=store,
        watchlist={PROVIDER_BYBIT: ("BTCUSDT",)}, transport=bybit,
        max_cycles=3, sleep=lambda seconds: None,
    )
    # A fresh wrapper each cycle would retry the failed primary three times.
    assert sum("api.bybit.com" in url for url in calls) == 1
    assert sum("api.bytick.com" in url for url in calls) == 3
    assert [summary["endpoint_stats"]["api.bytick.com"]["successes"]
            for summary in result["summaries"]] == [1, 2, 3]
    assert result["endpoint_stats"]["api.bybit.com"]["failures"] == 1


def test_injected_roaming_transport_is_preserved(bridge_on, store):
    def bybit(url, timeout):
        if "api.bybit.com" in url:
            raise RuntimeError("watch_source_unavailable")
        return BYBIT_TICKERS

    roaming = RoamingTransport(bybit)
    result = run_bridge(
        lambda line: None, store=store,
        watchlist={PROVIDER_BYBIT: ("BTCUSDT",)}, transport=roaming,
        max_cycles=2, sleep=lambda seconds: None,
    )
    assert result["endpoint_stats"] == roaming.stats()
    assert result["summaries"][0]["endpoint_stats"]["api.bybit.com"]["failures"] == 1
    assert result["endpoint_stats"]["api.bytick.com"]["successes"] == 2


def test_failover_does_not_bypass_ingestion_gate(bridge_on, store, monkeypatch):
    monkeypatch.delenv(INGEST_ENV, raising=False)

    def bybit(url, timeout):
        if "api.bybit.com" in url:
            raise RuntimeError("watch_source_unavailable")
        return BYBIT_TICKERS

    lines = []
    summary = broadcast_cycle(
        lines.append, store=store, watchlist={PROVIDER_BYBIT: ("BTCUSDT",)},
        transport=bybit,
    )
    assert summary["coverage"]["sources_ok"] == 1
    assert summary["endpoint_stats"]["api.bytick.com"]["successes"] == 1
    assert summary["broadcast"] == 0
    assert summary["rejected_local"] == 1
    assert lines == []


def test_writer_failure_is_propagated(bridge_on, store):
    def broken_writer(line):
        raise BrokenPipeError("node_stdin_closed")

    with pytest.raises(BrokenPipeError, match="node_stdin_closed"):
        broadcast_cycle(
            broken_writer, store=store, watchlist=WATCHLIST,
            transport=fake_transport,
        )
