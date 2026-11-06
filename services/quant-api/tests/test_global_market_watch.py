"""Tests for the global live-market watch layer (keyless public sources)."""
import json
from datetime import datetime, timezone

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.global_market_watch import (
    DEFAULT_WATCHLIST,
    PROVIDER_BINANCE,
    PROVIDER_BITSTAMP,
    PROVIDER_COINBASE,
    PROVIDER_ERAPI,
    PROVIDER_FRANKFURTER,
    PROVIDER_KRAKEN,
    PROVIDER_STOOQ,
    collect_market_observations,
    watch_and_ingest,
)
from osiris_api.p2p_ingestion import ENABLE_ENV

NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)

BINANCE_TICKER = json.dumps({
    "symbol": "BTCUSDT", "lastPrice": "69000.50",
    "priceChangePercent": "1.25", "volume": "1234.5",
}).encode()

FRANKFURTER_LATEST = json.dumps({
    "base": "USD", "date": "2026-11-06",
    "rates": {"EUR": 0.921, "GBP": 0.79, "JPY": 153.4, "INR": 84.1},
}).encode()

STOOQ_QUOTES = (
    "Symbol,Date,Time,Open,High,Low,Close,Volume\n"
    "^SPX,2026-11-06,21:00:00,5800.1,5820.0,5790.0,5810.5,0\n"
    "^NDQ,2026-11-06,21:00:00,N/D,N/D,N/D,N/D,N/D\n"
).encode()


def fake_transport(url: str, timeout: float) -> bytes:
    if "binance.com" in url:
        return BINANCE_TICKER
    if "frankfurter" in url:
        return FRANKFURTER_LATEST
    if "stooq.com" in url:
        return STOOQ_QUOTES
    raise RuntimeError("watch_source_unavailable")


SMALL_WATCHLIST = {
    PROVIDER_BINANCE: ("BTCUSDT",),
    PROVIDER_FRANKFURTER: ("EUR", "GBP", "JPY", "INR"),
    PROVIDER_STOOQ: ("^spx", "^ndq"),
}


@pytest.fixture
def store(tmp_path):
    return ResearchStore(str(tmp_path / "watch.sqlite3"))


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")


def test_collect_builds_contract_observations():
    result = collect_market_observations(
        watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW
    )
    assert result["errors"] == []
    instruments = {obs["instrument"] for obs in result["observations"]}
    # BTC + 4 FX pairs + ^SPX (ND row skipped as N/D, never fabricated)
    assert instruments == {"BTCUSDT", "USD-EUR", "USD-GBP", "USD-JPY", "USD-INR", "^SPX"}
    for obs in result["observations"]:
        assert obs["schema"] == "osiris.observation.v1"
        assert len(obs["payload_sha256"]) == 64
        assert obs["observed_at"] == NOW.isoformat()
    assert result["live_execution"] is False


def test_failing_source_isolated_others_continue():
    def broken_transport(url: str, timeout: float) -> bytes:
        if "binance.com" in url:
            raise RuntimeError("watch_source_unavailable")
        return fake_transport(url, timeout)

    result = collect_market_observations(
        watchlist=SMALL_WATCHLIST, transport=broken_transport, now=NOW
    )
    assert [e["source"] for e in result["errors"]] == [PROVIDER_BINANCE]
    assert any(obs["provider"] == PROVIDER_FRANKFURTER for obs in result["observations"])


def test_malformed_source_payload_recorded_not_raised():
    def garbage_transport(url: str, timeout: float) -> bytes:
        return b"not-json-not-csv"

    result = collect_market_observations(
        watchlist={PROVIDER_BINANCE: ("BTCUSDT",)},
        transport=garbage_transport, now=NOW,
    )
    assert result["observations"] == []
    assert result["errors"][0]["source"] == PROVIDER_BINANCE


def test_unknown_watch_source_fails_closed():
    with pytest.raises(ValueError, match="unknown_watch_source"):
        collect_market_observations(
            watchlist={"rogue-source": ("X",)}, transport=fake_transport, now=NOW
        )


def test_invalid_symbol_fails_closed():
    result = collect_market_observations(
        watchlist={PROVIDER_BINANCE: ("bad symbol!",)},
        transport=fake_transport, now=NOW,
    )
    assert result["errors"][0]["error"] == "invalid_binance_symbol"


def test_watch_and_ingest_full_cycle(store, enabled):
    summary = watch_and_ingest(
        store, watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW
    )
    assert summary["fetched"] == 6
    assert summary["ingested"] == 6
    assert summary["rejected"] == []
    assert summary["live_execution"] is False
    assert summary["broker_order_submitted"] is False


def test_second_identical_cycle_skips_duplicates(store, enabled):
    first = watch_and_ingest(
        store, watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW
    )
    second = watch_and_ingest(
        store, watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW
    )
    assert first["ingested"] == 6
    assert second["ingested"] == 0
    assert second["duplicates_skipped"] == 6


def test_kill_switch_blocks_everything(store, monkeypatch):
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    summary = watch_and_ingest(
        store, watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW
    )
    assert summary["ingested"] == 0
    assert all("kill_switch" in item["reason"] for item in summary["rejected"])


def test_provider_allowlist_enforced(store, enabled):
    summary = watch_and_ingest(
        store, watchlist=SMALL_WATCHLIST, transport=fake_transport, now=NOW,
        allowed_providers={PROVIDER_BINANCE},
    )
    assert summary["ingested"] == 1
    assert len(summary["rejected"]) == 5


def test_default_watchlist_shape():
    assert set(DEFAULT_WATCHLIST) == {
        PROVIDER_BINANCE, PROVIDER_FRANKFURTER, PROVIDER_STOOQ,
        PROVIDER_COINBASE, PROVIDER_KRAKEN, PROVIDER_BITSTAMP, PROVIDER_ERAPI,
    }
    for symbols in DEFAULT_WATCHLIST.values():
        assert symbols and all(isinstance(symbol, str) for symbol in symbols)
