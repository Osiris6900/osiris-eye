"""Coverage must expose total and partial blindness without inventing quotes."""
import json
from datetime import UTC, datetime

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.global_market_watch import collect_market_observations, watch_and_ingest
from osiris_api.p2p_ingestion import ENABLE_ENV
from osiris_api.watch_coverage import coverage_report

NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)


def observation(provider, instrument):
    return {"provider": provider, "instrument": instrument}


def test_partial_coverage_counts_independent_sources_and_completely_missing_instruments():
    report = coverage_report(
        {"coinbase-public": ("BTC", "ETH"), "kraken-public": ("BTC", "SOL")},
        {"observations": [
            observation("coinbase-public", "BTC"),
            observation("kraken-public", "BTC"),
            observation("coinbase-public", "ETH"),
        ]},
    )
    assert report["instruments_watched"] == 3
    assert report["instruments_covered"] == 2
    assert report["instruments_missing"] == 1
    assert report["missing_instruments"] == ["SOL"]
    assert report["instrument_sources"] == {
        "BTC": ["coinbase-public", "kraken-public"], "ETH": ["coinbase-public"],
    }
    assert report["single_source_instruments"] == ["ETH"]


def test_total_blindness_distinguishes_failed_and_silent_sources():
    report = coverage_report(
        {"bybit-public": ("BTCUSDT",), "okx-public": ("BTCUSDT", "ETHUSDT")},
        {"observations": [], "errors": [{"source": "bybit-public", "error": "offline"}]},
    )
    assert report["sources_ok"] == 0
    assert report["failed_sources"] == ["bybit-public"]
    assert report["silent_sources"] == ["okx-public"]
    assert report["per_source"]["bybit-public"] == {
        "quotes": 0, "status": "error", "error": "offline",
    }
    assert report["missing_instruments"] == ["BTCUSDT", "ETHUSDT"]
    assert report["instruments_missing"] == report["instruments_watched"] == 2
    assert report["instruments_covered"] == 0


def test_unwatched_and_malformed_observations_cannot_hide_blind_spots():
    report = coverage_report(
        {"coinbase-public": ("BTC",), "kraken-public": ("ETH",)},
        {"observations": [
            observation("kraken-public", "BTC"),  # wanted, but not from this provider
            observation("rogue-provider", "ETH"),
            observation("coinbase-public", "SOL"),
            observation(["coinbase-public"], "BTC"),
            observation("coinbase-public", {"instrument": "BTC"}),
            None,
        ], "errors": [None, {"source": "rogue-provider", "error": "offline"}]},
    )
    assert report["instruments_covered"] == 0
    assert report["missing_instruments"] == ["BTC", "ETH"]
    assert report["failed_sources"] == []
    assert report["silent_sources"] == ["coinbase-public", "kraken-public"]
    assert all(source["quotes"] == 0 for source in report["per_source"].values())


def test_canonical_names_match_each_collector_without_merging_usd_and_usdt():
    watchlist = {
        "frankfurter-ecb": (" eur ",), "erapi-public": ("EUR",),
        "stooq-public": (" ^spx ",),
        "coinbase-public": (" btc ",), "kraken-public": ("btc",),
        "bitstamp-public": ("BTC",), "binance-public": (" btcusdt ",),
        "okx-public": ("btcusdt",), "bybit-public": ("BTCUSDT",),
    }
    expected = {
        "USD-EUR": ["erapi-public", "frankfurter-ecb"], "^SPX": ["stooq-public"],
        "BTC": ["bitstamp-public", "coinbase-public", "kraken-public"],
        "BTCUSDT": ["binance-public", "bybit-public", "okx-public"],
    }
    report = coverage_report(watchlist, {"observations": [
        observation(provider, instrument)
        for instrument, providers in expected.items() for provider in providers
    ]})
    assert report["instrument_sources"] == expected
    assert report["instruments_watched"] == report["instruments_covered"] == 4
    assert report["missing_instruments"] == []
    assert report["sources_ok"] == 9


def test_repeated_quotes_and_watch_symbols_do_not_inflate_instrument_totals():
    report = coverage_report(
        {"p1": ("A", " a ")},
        {"observations": [observation("p1", "A"), observation("p1", "A")]},
    )
    assert report["instruments_watched"] == report["instruments_covered"] == 1
    assert report["instrument_sources"] == {"A": ["p1"]}
    assert report["single_source_instruments"] == ["A"]


def partial_transport(url, timeout):
    if "bybit.com" in url:
        raise RuntimeError("watch_source_unavailable")
    if "kraken.com" in url:
        return json.dumps({"error": [], "result": {"XXBTZUSD": {"c": ["69000"]}}}).encode()
    raise AssertionError("unexpected transport request")


PARTIAL_WATCHLIST = {"kraken-public": ("BTC", "ETH"), "bybit-public": ("BTCUSDT",)}


def test_collection_reports_partial_and_total_source_blindness_together():
    result = collect_market_observations(
        watchlist=PARTIAL_WATCHLIST, transport=partial_transport, now=NOW,
    )
    report = result["coverage"]
    assert [obs["instrument"] for obs in result["observations"]] == ["BTC"]
    assert report["sources_ok"] == 1
    assert report["failed_sources"] == ["bybit-public"]
    assert report["missing_instruments"] == ["BTCUSDT", "ETH"]
    assert report["instruments_watched"] == 3
    assert report["instruments_covered"] == 1


@pytest.mark.parametrize("enabled", [True, False])
def test_ingestion_retains_collection_coverage_even_when_ingestion_is_disabled(
    tmp_path, monkeypatch, enabled,
):
    monkeypatch.setenv(ENABLE_ENV, "1" if enabled else "0")
    result = watch_and_ingest(
        ResearchStore(str(tmp_path / "watch.sqlite3")),
        watchlist=PARTIAL_WATCHLIST, transport=partial_transport, now=NOW,
    )
    assert result["ingested"] == int(enabled)
    assert result["coverage"]["instrument_sources"] == {"BTC": ["kraken-public"]}
    assert result["coverage"]["missing_instruments"] == ["BTCUSDT", "ETH"]
    assert result["coverage"]["failed_sources"] == ["bybit-public"]
    assert result["live_execution"] is False


def test_explicit_empty_watchlist_stays_empty_without_network_requests(tmp_path):
    def forbidden_transport(url, timeout):
        raise AssertionError("an empty watchlist must not fetch default sources")

    result = watch_and_ingest(
        ResearchStore(str(tmp_path / "empty.sqlite3")),
        watchlist={}, transport=forbidden_transport, now=NOW,
    )
    assert result["fetched"] == result["ingested"] == 0
    assert result["source_errors"] == []
    assert result["coverage"]["sources_watched"] == 0
    assert result["coverage"]["instruments_watched"] == 0
    assert result["coverage"]["missing_instruments"] == []
