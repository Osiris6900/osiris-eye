"""Tests for multi-source keyless providers and weighted mesh consensus."""
import hashlib
import json
from datetime import datetime, timezone

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.global_market_watch import (
    PROVIDER_BITSTAMP,
    PROVIDER_COINBASE,
    PROVIDER_ERAPI,
    PROVIDER_KRAKEN,
    collect_market_observations,
    watch_and_ingest,
)
from osiris_api.mesh_market_intelligence import (
    consensus_price,
    source_weights_from_reports,
    weighted_consensus_price,
)
from osiris_api.p2p_ingestion import ENABLE_ENV

NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)

COINBASE_TICKER = json.dumps({
    "price": "69112.50", "time": "2026-11-06T11:59:58.123456Z",
}).encode()

KRAKEN_TICKER = json.dumps({
    "error": [],
    "result": {
        "XXBTZUSD": {"c": ["69100.0", "0.012"]},
        "XETHZUSD": {"c": ["3410.5", "1.2"]},
    },
}).encode()

KRAKEN_ERROR = json.dumps({"error": ["EQuery:Unknown asset pair"], "result": {}}).encode()

BITSTAMP_TICKER = json.dumps({"last": "69098.25", "timestamp": "1762444799"}).encode()

ERAPI_LATEST = json.dumps({
    "result": "success", "base_code": "USD",
    "rates": {"EUR": 0.9215, "GBP": 0.7905, "JPY": 153.35},
}).encode()


def fake_transport(url: str, timeout: float) -> bytes:
    if "coinbase" in url:
        return COINBASE_TICKER
    if "kraken.com" in url:
        return KRAKEN_TICKER
    if "bitstamp" in url:
        return BITSTAMP_TICKER
    if "er-api.com" in url:
        return ERAPI_LATEST
    raise RuntimeError("watch_source_unavailable")


MULTI_WATCHLIST = {
    PROVIDER_COINBASE: ("BTC",),
    PROVIDER_KRAKEN: ("BTC", "ETH"),
    PROVIDER_BITSTAMP: ("BTC",),
    PROVIDER_ERAPI: ("EUR", "GBP", "JPY"),
}


@pytest.fixture
def store(tmp_path):
    return ResearchStore(str(tmp_path / "multi.sqlite3"))


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")


# --- new keyless providers ---------------------------------------------------

def test_collect_multi_source_crypto_canonical_instruments():
    result = collect_market_observations(
        watchlist=MULTI_WATCHLIST, transport=fake_transport, now=NOW,
    )
    assert result["errors"] == []
    instruments = [obs["instrument"] for obs in result["observations"]]
    # providers alphabetical: bitstamp, coinbase, erapi, kraken
    assert instruments == ["BTC", "BTC", "USD-EUR", "USD-GBP", "USD-JPY", "BTC", "ETH"]
    btc_obs = [obs for obs in result["observations"] if obs["instrument"] == "BTC"]
    assert {obs["provider"] for obs in btc_obs} == {
        PROVIDER_BITSTAMP, PROVIDER_COINBASE, PROVIDER_KRAKEN,
    }
    for observation in result["observations"]:
        assert observation["payload_sha256"] == hashlib.sha256(
            observation["payload"].encode("utf-8")
        ).hexdigest()


def test_canonical_instrument_three_sources_same_symbol():
    """The whole point: one canonical BTC instrument, three independent
    USD-quoted sources → real median consensus becomes possible."""
    result = collect_market_observations(
        watchlist=MULTI_WATCHLIST, transport=fake_transport, now=NOW,
    )
    reports = []
    for obs in result["observations"]:
        if obs["instrument"] != "BTC":
            continue
        quote = json.loads(obs["payload"])
        reports.append({"observer": obs["provider"], "source": obs["provider"],
                        "value": quote["last"]})
    verdict = consensus_price(reports, min_observers=3, min_sources=3)
    assert verdict["accepted"] is True
    assert verdict["sources"] == 3
    assert verdict["price"] == pytest.approx(69100.0, rel=0.001)


def test_coinbase_payload_honest_pair():
    result = collect_market_observations(
        watchlist={PROVIDER_COINBASE: ("BTC",)}, transport=fake_transport, now=NOW,
    )
    quote = json.loads(result["observations"][0]["payload"])
    assert quote["last"] == 69112.5
    assert quote["pair"] == "BTC-USD"
    assert quote["source"] == "coinbase_exchange_ticker"


def test_kraken_decorated_result_keys():
    result = collect_market_observations(
        watchlist={PROVIDER_KRAKEN: ("BTC", "ETH")}, transport=fake_transport, now=NOW,
    )
    quotes = {obs["instrument"]: json.loads(obs["payload"])
              for obs in result["observations"]}
    assert quotes["BTC"]["last"] == 69100.0
    assert quotes["ETH"]["last"] == 3410.5
    assert quotes["BTC"]["pair"] == "XBTUSD"


def test_kraken_api_error_reported():
    def error_transport(url: str, timeout: float) -> bytes:
        return KRAKEN_ERROR
    result = collect_market_observations(
        watchlist={PROVIDER_KRAKEN: ("BTC",)}, transport=error_transport, now=NOW,
    )
    assert result["observations"] == []
    assert "invalid_kraken_ticker_response" in result["errors"][0]["error"]


def test_bitstamp_payload():
    result = collect_market_observations(
        watchlist={PROVIDER_BITSTAMP: ("BTC",)}, transport=fake_transport, now=NOW,
    )
    quote = json.loads(result["observations"][0]["payload"])
    assert quote["last"] == 69098.25
    assert quote["pair"] == "btcusd"


def test_erapi_cross_source_fx_instruments():
    result = collect_market_observations(
        watchlist={PROVIDER_ERAPI: ("EUR",)}, transport=fake_transport, now=NOW,
    )
    observation = result["observations"][0]
    assert observation["instrument"] == "USD-EUR"  # same as frankfurter-ecb
    quote = json.loads(observation["payload"])
    assert quote["rate_per_usd"] == 0.9215
    assert quote["source"] == "open_er_api_latest"


def test_invalid_canonical_symbol_rejected():
    result = collect_market_observations(
        watchlist={PROVIDER_COINBASE: ("DOGE",)}, transport=fake_transport, now=NOW,
    )
    assert "invalid_canonical_crypto_symbol" in result["errors"][0]["error"]


def test_watch_and_ingest_multi_source(store, enabled):
    report = watch_and_ingest(
        store, watchlist=MULTI_WATCHLIST, transport=fake_transport, now=NOW,
    )
    assert report["fetched"] == 7
    assert report["ingested"] == 7
    assert report["rejected"] == []


# --- source health weights ----------------------------------------------------

def _reports(values_by_source):
    reports = []
    for source, values in values_by_source.items():
        for value in values:
            reports.append({"observer": source, "source": source, "value": value})
    return reports


def test_source_weights_reward_agreement():
    reports = _reports({
        "steady-a": [100.0, 100.01, 99.99],
        "steady-b": [100.02, 100.0, 100.01],
        "drifter": [100.0, 100.9, 99.1],
    })
    weights = source_weights_from_reports(reports)
    assert weights["steady-a"] == 2.0  # clamped at the top
    assert weights["steady-b"] == 2.0
    assert weights["drifter"] < 1.0
    assert all(0.5 <= w <= 2.0 for w in weights.values())


def test_source_weights_liar_sink_to_floor():
    reports = _reports({
        "honest-a": [100.0, 100.0],
        "honest-b": [100.0, 100.0],
        "liar": [150.0, 160.0],
    })
    weights = source_weights_from_reports(reports)
    assert weights["liar"] == 0.5  # clamped at the bottom


def test_source_weights_validation():
    with pytest.raises(ValueError, match="weights_require_reports"):
        source_weights_from_reports([])
    with pytest.raises(ValueError, match="invalid_consensus_report"):
        source_weights_from_reports([{"source": "x", "value": -5}])
    with pytest.raises(ValueError, match="invalid_weight_bounds"):
        source_weights_from_reports(
            [{"source": "x", "value": 1}], min_weight=2.0, max_weight=0.5,
        )


# --- weighted consensus -------------------------------------------------------

def test_weighted_consensus_none_equals_plain():
    reports = _reports({
        "a": [100.0, 100.1], "b": [100.05], "c": [99.95],
    })
    plain = consensus_price(reports, min_observers=3, min_sources=3)
    weighted = weighted_consensus_price(reports, weights=None,
                                        min_observers=3, min_sources=3)
    assert weighted == plain


def test_weighted_consensus_pulls_toward_trusted_source():
    reports = _reports({
        "trusted": [100.4, 100.4],
        "plain-a": [100.0],
        "plain-b": [100.1],
    })
    weighted = weighted_consensus_price(
        reports, weights={"trusted": 2.0}, min_observers=3, min_sources=3,
    )
    assert weighted["accepted"] is True
    assert weighted["method"] == "weighted_median_of_inliers"
    # plain median would be 100.2; trusted source's 2.0 weight pulls it up
    assert weighted["price"] == pytest.approx(100.4)
    assert weighted["weights_used"]["trusted"] == 2.0
    assert weighted["weights_used"]["plain-a"] == 1.0


def test_weighted_consensus_cannot_override_inlier_gate():
    reports = _reports({
        "honest-a": [100.0, 100.05],
        "honest-b": [99.95],
        "manipulator": [130.0, 130.0],
    })
    weighted = weighted_consensus_price(
        reports, weights={"manipulator": 2.0}, min_observers=3, min_sources=3,
    )
    assert weighted["accepted"] is True
    # 130 is outside the 1% inlier band no matter the weight
    assert weighted["price"] == pytest.approx(100.0)
    assert weighted["outliers_rejected"] == 2


def test_weighted_consensus_clamps_weights():
    reports = _reports({"a": [100.0], "b": [100.1], "c": [100.2]})
    weighted = weighted_consensus_price(
        reports, weights={"a": 999.0, "b": -7.0, "c": float("nan")},
        min_observers=3, min_sources=3,
    )
    assert weighted["weights_used"] == {"a": 2.0, "b": 0.5, "c": 1.0}


def test_weighted_consensus_quorums_enforced():
    few = _reports({"only-one": [100.0, 100.1]})
    verdict = weighted_consensus_price(
        few, weights={"only-one": 2.0}, min_observers=3, min_sources=2,
    )
    assert verdict["accepted"] is False
    assert verdict["reason"] == "observer_quorum_not_met"


def test_weighted_consensus_rejects_invalid_reports():
    with pytest.raises(ValueError, match="invalid_consensus_report"):
        weighted_consensus_price(
            [{"source": "", "value": 100}], weights={}, min_observers=1,
        )
