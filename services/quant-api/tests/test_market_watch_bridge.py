"""Tests for the autonomous market-watch → P2P node bridge."""
import json

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.global_market_watch import (
    PROVIDER_BINANCE,
    PROVIDER_FRANKFURTER,
    PROVIDER_STOOQ,
)
from osiris_api.market_watch_bridge import (
    ENABLE_ENV as BRIDGE_ENV,
    MAX_INTERVAL_SECONDS,
    MIN_INTERVAL_SECONDS,
    BridgeRefused,
    broadcast_cycle,
    run_bridge,
)
from osiris_api.p2p_ingestion import ENABLE_ENV as INGEST_ENV

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


@pytest.fixture
def store(tmp_path):
    return ResearchStore(str(tmp_path / "bridge.sqlite3"))


def test_cycle_broadcasts_every_observation(bridge_on):
    lines: list[str] = []
    summary = broadcast_cycle(
        lines.append, watchlist=WATCHLIST, transport=fake_transport
    )
    assert summary["broadcast"] == 3
    assert summary["source_errors"] == []
    assert summary["live_execution"] is False
    parsed = [json.loads(line) for line in lines]
    assert {obs["instrument"] for obs in parsed} == {"BTCUSDT", "USD-EUR", "^SPX"}
    assert all(obs["schema"] == "osiris.observation.v1" for obs in parsed)


def test_cycle_refused_without_kill_switch(monkeypatch):
    monkeypatch.delenv(BRIDGE_ENV, raising=False)
    with pytest.raises(BridgeRefused, match="kill_switch"):
        broadcast_cycle(lambda line: None, watchlist=WATCHLIST,
                        transport=fake_transport)


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


def test_source_failure_isolated_others_broadcast(bridge_on):
    def broken(url: str, timeout: float) -> bytes:
        if "stooq.com" in url:
            raise RuntimeError("watch_source_unavailable")
        return fake_transport(url, timeout)

    lines: list[str] = []
    summary = broadcast_cycle(
        lines.append, watchlist=WATCHLIST, transport=broken
    )
    assert summary["broadcast"] == 2
    assert [e["source"] for e in summary["source_errors"]] == [PROVIDER_STOOQ]


def test_run_bridge_bounds_fail_closed(bridge_on):
    with pytest.raises(BridgeRefused, match="interval_out_of_bounds"):
        run_bridge(lambda line: None, interval_seconds=MIN_INTERVAL_SECONDS - 1)
    with pytest.raises(BridgeRefused, match="interval_out_of_bounds"):
        run_bridge(lambda line: None, interval_seconds=MAX_INTERVAL_SECONDS + 1)
    with pytest.raises(BridgeRefused, match="max_cycles_out_of_bounds"):
        run_bridge(lambda line: None, max_cycles=0)


def test_run_bridge_cycles_and_sleep(bridge_on):
    lines: list[str] = []
    sleeps: list[float] = []
    result = run_bridge(
        lines.append, watchlist=WATCHLIST, transport=fake_transport,
        interval_seconds=MIN_INTERVAL_SECONDS, max_cycles=3, sleep=sleeps.append,
    )
    assert result["cycles"] == 3
    assert len(lines) == 9
    assert sleeps == [MIN_INTERVAL_SECONDS] * 3
    assert all(s["broadcast"] == 3 for s in result["summaries"])
