import pytest

from osiris_api.market_gateway import MarketDataGateway, MarketDataUnavailable
from osiris_api.universal_aggregator import UniversalMarketDataAggregator


class Adapter:
    mode = "public"

    def __init__(self, bars=None, error=None):
        self.bars = bars or []
        self.error = error

    def fetch_bars(self, symbol, start, end):
        if self.error:
            raise self.error
        return self.bars


def bar(close=101):
    return {
        "symbol": "BTC-USD",
        "event_time": "2026-11-02T10:00:00Z",
        "available_at": "2026-11-02T10:00:01Z",
        "open": 100, "high": max(102, close), "low": 99,
        "close": close, "volume": 10,
    }


def gateway():
    return MarketDataGateway(
        {"primary": Adapter([bar(100)]), "backup": Adapter([bar(101)]),
         "offline": Adapter(error=RuntimeError("upstream down"))},
        {"primary": "public:primary", "backup": "public:backup",
         "offline": "public:offline"},
    )


def test_aggregator_preserves_provider_data_and_flags_divergence():
    result = UniversalMarketDataAggregator(gateway(), divergence_threshold_bps=50).aggregate_bars(
        providers=["primary", "backup"], symbol="BTC-USD",
        start="2026-11-02T09:00:00Z", end="2026-11-02T11:00:00Z",
        asset_class="crypto",
    )
    assert result["primary_provider"] == "primary"
    assert result["bars"][0]["close"] == 100
    assert result["quality"]["divergence_count"] == 1
    assert result["quality"]["price_blending"] is False
    assert result["provider_datasets"]["backup"]["bars"][0]["close"] == 101


def test_aggregator_keeps_working_when_one_provider_is_unavailable():
    result = UniversalMarketDataAggregator(gateway()).aggregate_bars(
        providers=["offline", "primary"], symbol="BTC-USD",
        start="2026-11-02T09:00:00Z", end="2026-11-02T11:00:00Z",
        asset_class="crypto",
    )
    assert result["primary_provider"] == "primary"
    assert result["providers"][0]["status"] == "unavailable"
    assert result["providers"][1]["status"] == "ok"


def test_aggregator_fails_closed_when_no_provider_is_available():
    with pytest.raises(MarketDataUnavailable, match="all_aggregation_providers_unavailable"):
        UniversalMarketDataAggregator(MarketDataGateway()).aggregate_bars(
            providers=["missing"], symbol="BTC-USD",
            start="2026-11-02T09:00:00Z", end="2026-11-02T11:00:00Z",
        )


def test_aggregator_rejects_duplicate_provider_ids():
    with pytest.raises(ValueError, match="duplicate_provider_in_list"):
        UniversalMarketDataAggregator(gateway()).aggregate_bars(
            providers=["primary", "primary"], symbol="BTC-USD",
            start="2026-11-02T09:00:00Z", end="2026-11-02T11:00:00Z",
        )
