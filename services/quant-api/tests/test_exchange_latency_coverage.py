"""Exchange coverage (OKX, Bybit) + fetch-latency telemetry + latency report.

The watch layer measures the node-to-source HTTP round-trip for every
source, stamps it into each signed quote payload, and the mesh turns those
stamps into a per-source latency ranking. This is honest network telemetry
from real node locations — never physical exchange co-location.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.global_market_watch import (
    DEFAULT_PROVIDERS,
    DEFAULT_WATCHLIST,
    PROVIDER_BYBIT,
    PROVIDER_OKX,
    collect_market_observations,
    watch_and_ingest,
)
from osiris_api.mesh_market_intelligence import source_latency_report
from osiris_api.p2p_ingestion import ENABLE_ENV, ingest_p2p_observation

NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)

OKX_TICKERS = json.dumps({
    "code": "0",
    "data": [
        {"instId": "BTC-USDT", "last": "69001.0", "volCcy24h": "12000.5"},
        {"instId": "ETH-USDT", "last": "2501.25", "volCcy24h": "88000.0"},
        {"instId": "DOGE-USDT", "last": "0.19", "volCcy24h": "999"},
    ],
}).encode()

BYBIT_TICKERS = json.dumps({
    "retCode": 0,
    "result": {
        "list": [
            {"symbol": "BTCUSDT", "lastPrice": "69002.5", "volume24h": "11000.0"},
            {"symbol": "ETHUSDT", "lastPrice": "2500.75", "volume24h": "77000.0"},
            {"symbol": "DOGEUSDT", "lastPrice": "0.18", "volume24h": "555"},
        ]
    },
}).encode()

WATCHLIST = {
    PROVIDER_OKX: ("BTCUSDT", "ETHUSDT"),
    PROVIDER_BYBIT: ("BTCUSDT", "ETHUSDT"),
}


def exchange_transport(url: str, timeout: float) -> bytes:
    if "okx.com" in url:
        return OKX_TICKERS
    if "bybit.com" in url:
        return BYBIT_TICKERS
    raise RuntimeError("watch_source_unavailable")


@pytest.fixture
def store(tmp_path):
    return ResearchStore(str(tmp_path / "latency.sqlite3"))


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")


def _observation(provider: str, instrument: str, quote: dict,
                 observed_at: datetime) -> dict:
    payload = json.dumps(quote, allow_nan=False, separators=(",", ":"), sort_keys=True)
    return {
        "schema": "osiris.observation.v1",
        "instrument": instrument,
        "observed_at": observed_at.isoformat(),
        "provider": provider,
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


def _ingest(store, provider: str, instrument: str, quote: dict,
            observed_at: datetime, *, now: datetime) -> None:
    ingest_p2p_observation(
        store,
        _observation(provider, instrument, quote, observed_at),
        allowed_providers={provider},
        now=now,
    )


# ------------------------------------------------------------------ coverage

def test_default_watchlist_covers_nine_keyless_sources():
    assert set(DEFAULT_WATCHLIST) == set(DEFAULT_PROVIDERS)
    assert len(DEFAULT_PROVIDERS) == 9
    assert PROVIDER_OKX in DEFAULT_WATCHLIST
    assert PROVIDER_BYBIT in DEFAULT_WATCHLIST


def test_okx_batch_fetch_builds_shared_usdt_instruments():
    result = collect_market_observations(
        watchlist={PROVIDER_OKX: ("BTCUSDT", "ETHUSDT")},
        transport=exchange_transport, now=NOW,
    )
    assert result["errors"] == []
    observations = result["observations"]
    # DOGE-USDT is returned by the venue but not watched: ignored, never kept.
    assert {obs["instrument"] for obs in observations} == {"BTCUSDT", "ETHUSDT"}
    for obs in observations:
        assert obs["provider"] == PROVIDER_OKX
        quote = json.loads(obs["payload"])
        assert quote["pair"].endswith("-USDT")
        assert quote["source"] == "okx_v5_spot_tickers"
        assert quote["last"] > 0


def test_okx_rejects_non_usdt_watchlist_symbol():
    result = collect_market_observations(
        watchlist={PROVIDER_OKX: ("BTCUSD",)},
        transport=exchange_transport, now=NOW,
    )
    assert result["observations"] == []
    assert result["errors"][0]["error"] == "invalid_okx_symbol"


def test_okx_error_code_and_malformed_body_recorded():
    def bad_code(url: str, timeout: float) -> bytes:
        return json.dumps({"code": "1", "data": []}).encode()

    result = collect_market_observations(
        watchlist={PROVIDER_OKX: ("BTCUSDT",)}, transport=bad_code, now=NOW
    )
    assert result["errors"][0]["error"] == "invalid_okx_ticker_response"

    result = collect_market_observations(
        watchlist={PROVIDER_OKX: ("BTCUSDT",)},
        transport=lambda url, timeout: b"\x00\xff not json",
        now=NOW,
    )
    assert result["errors"][0]["error"] == "invalid_okx_ticker_response"


def test_okx_non_finite_last_fails_closed():
    def bad_price(url: str, timeout: float) -> bytes:
        return json.dumps({
            "code": "0",
            "data": [{"instId": "BTC-USDT", "last": "-5"}],
        }).encode()

    result = collect_market_observations(
        watchlist={PROVIDER_OKX: ("BTCUSDT",)}, transport=bad_price, now=NOW
    )
    assert result["observations"] == []
    assert result["errors"][0]["error"] == "invalid_quote_value"


def test_bybit_batch_fetch_builds_shared_usdt_instruments():
    result = collect_market_observations(
        watchlist={PROVIDER_BYBIT: ("BTCUSDT", "ETHUSDT")},
        transport=exchange_transport, now=NOW,
    )
    assert result["errors"] == []
    observations = result["observations"]
    assert {obs["instrument"] for obs in observations} == {"BTCUSDT", "ETHUSDT"}
    for obs in observations:
        assert obs["provider"] == PROVIDER_BYBIT
        quote = json.loads(obs["payload"])
        assert quote["pair"] in {"BTCUSDT", "ETHUSDT"}
        assert quote["source"] == "bybit_v5_spot_tickers"
        assert quote["last"] > 0


def test_bybit_rejects_non_usdt_watchlist_symbol():
    result = collect_market_observations(
        watchlist={PROVIDER_BYBIT: ("BTC-PERP",)},
        transport=exchange_transport, now=NOW,
    )
    assert result["observations"] == []
    assert result["errors"][0]["error"] == "invalid_bybit_symbol"


def test_bybit_bad_retcode_and_bad_result_shape_recorded():
    def bad_code(url: str, timeout: float) -> bytes:
        return json.dumps({"retCode": 10001, "result": {"list": []}}).encode()

    result = collect_market_observations(
        watchlist={PROVIDER_BYBIT: ("BTCUSDT",)}, transport=bad_code, now=NOW
    )
    assert result["errors"][0]["error"] == "invalid_bybit_ticker_response"

    def bad_shape(url: str, timeout: float) -> bytes:
        return json.dumps({"retCode": 0, "result": ["not-a-dict"]}).encode()

    result = collect_market_observations(
        watchlist={PROVIDER_BYBIT: ("BTCUSDT",)}, transport=bad_shape, now=NOW
    )
    assert result["errors"][0]["error"] == "invalid_bybit_ticker_response"


# ------------------------------------------------------------------ latency

def test_latency_stamped_into_signed_payload_and_summarized():
    result = collect_market_observations(
        watchlist=WATCHLIST, transport=exchange_transport, now=NOW
    )
    assert result["errors"] == []
    assert [entry["source"] for entry in result["latencies"]] == sorted(WATCHLIST)
    for entry in result["latencies"]:
        assert entry["ok"] is True
        assert entry["quotes"] == 2
        assert entry["fetch_roundtrip_ms"] >= 0
    for obs in result["observations"]:
        quote = json.loads(obs["payload"])
        assert quote["fetch_roundtrip_ms"] >= 0
        # latency is part of the signed payload: hash must match
        assert hashlib.sha256(obs["payload"].encode("utf-8")).hexdigest() == obs["payload_sha256"]


def test_failed_source_still_gets_latency_entry():
    def down(url: str, timeout: float) -> bytes:
        raise RuntimeError("watch_source_unavailable")

    result = collect_market_observations(
        watchlist={PROVIDER_OKX: ("BTCUSDT",)}, transport=down, now=NOW
    )
    assert result["errors"][0]["source"] == PROVIDER_OKX
    entry = result["latencies"][0]
    assert entry["source"] == PROVIDER_OKX
    assert entry["ok"] is False
    assert entry["fetch_roundtrip_ms"] >= 0


def test_payload_latency_is_bucketed_so_unchanged_market_dedupes(store, enabled):
    # Two identical cycles with a fast (sub-5 ms) fake transport must yield
    # identical payload hashes: latency noise must not defeat dedupe.
    first = watch_and_ingest(
        store,
        allowed_providers={PROVIDER_OKX, PROVIDER_BYBIT},
        watchlist=WATCHLIST, transport=exchange_transport, now=NOW,
    )
    second = watch_and_ingest(
        store,
        allowed_providers={PROVIDER_OKX, PROVIDER_BYBIT},
        watchlist=WATCHLIST, transport=exchange_transport, now=NOW,
    )
    assert first["ingested"] == 4
    assert second["ingested"] == 0
    assert second["duplicates_skipped"] == 4
    rows = store.list("p2p_observations", limit=10)
    for row in rows:
        inner = json.loads(row["payload"]["payload"])
        stamped = inner["fetch_roundtrip_ms"]
        assert stamped >= 0
        assert round(stamped / 5.0) * 5.0 == stamped  # 5 ms buckets


def test_watch_and_ingest_stores_latency_inside_payload(store, enabled):
    result = watch_and_ingest(
        store,
        allowed_providers={PROVIDER_OKX, PROVIDER_BYBIT},
        watchlist=WATCHLIST,
        transport=exchange_transport,
        now=NOW,
    )
    assert result["fetched"] == result["ingested"] == 4
    assert result["source_errors"] == []
    assert len(result["latencies"]) == 2
    rows = store.list("p2p_observations", limit=10)
    assert len(rows) == 4
    for row in rows:
        inner = json.loads(row["payload"]["payload"])
        assert inner["fetch_roundtrip_ms"] >= 0


# ----------------------------------------------------------- latency report

def test_source_latency_report_ranks_nearest_first(store, enabled):
    base = NOW - timedelta(minutes=2)
    for offset in range(3):
        moment = base + timedelta(seconds=offset)
        _ingest(store, "fast-source", "BTC", {
            "last": 69000.0 + offset, "fetch_roundtrip_ms": 40.0,
        }, moment, now=moment)
    for offset in range(2):
        moment = base + timedelta(seconds=10 + offset)
        _ingest(store, "slow-source", "BTC", {
            "last": 69010.0 + offset, "fetch_roundtrip_ms": 200.0 + 10 * offset,
        }, moment, now=moment)

    report = source_latency_report(store)
    assert report["rows_scanned"] == 5
    assert report["rows_without_latency"] == 0
    assert report["nearest_sources"] == ["fast-source", "slow-source"]
    fast, slow = report["sources"]
    assert fast["provider"] == "fast-source"
    assert fast["samples"] == 3
    assert fast["min_ms"] == fast["median_ms"] == 40.0
    assert slow["provider"] == "slow-source"
    assert slow["median_ms"] == 205.0
    assert slow["last_ms"] == 210.0
    assert slow["last_seen"] > fast["last_seen"]
    assert report["co_location"] is False
    assert report["live_execution"] is False


def test_source_latency_report_skips_payloads_without_latency(store, enabled):
    _ingest(store, "plain-source", "BTC", {"last": 69000.0}, NOW, now=NOW)
    report = source_latency_report(store)
    assert report["rows_scanned"] == 1
    assert report["rows_without_latency"] == 1
    assert report["sources"] == []
    assert report["nearest_sources"] == []


def test_source_latency_report_rejects_bad_latency_values(store, enabled):
    _ingest(store, "odd-source", "BTC", {
        "last": 69000.0, "fetch_roundtrip_ms": -1,
    }, NOW, now=NOW)
    report = source_latency_report(store)
    assert report["sources"] == []
    assert report["rows_without_latency"] == 1


def test_source_latency_report_validates_bounds(store):
    with pytest.raises(ValueError, match="invalid_latency_max_rows"):
        source_latency_report(store, max_rows=0)
    with pytest.raises(ValueError, match="invalid_latency_max_rows"):
        source_latency_report(store, max_rows=10_001)
