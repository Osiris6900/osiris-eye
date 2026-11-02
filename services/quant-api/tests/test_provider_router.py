import pytest

from osiris_api.market_gateway import MarketDataGateway, MarketDataUnavailable
from osiris_api.provider_router import MultiProviderMarketDataRouter


class Adapter:
    mode = "sandbox"

    def __init__(self, bars):
        self.bars = bars

    def fetch_bars(self, symbol, start, end):
        if isinstance(self.bars, Exception):
            raise self.bars
        return self.bars


def valid_bar():
    return {
        "symbol": "ABC", "event_time": "2026-11-02T10:00:00Z",
        "available_at": "2026-11-02T10:00:01Z",
        "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100,
    }


def test_router_fails_over_in_priority_order_and_records_provenance():
    gateway = MarketDataGateway(
        {"primary": Adapter(RuntimeError("offline")), "backup": Adapter([valid_bar()])},
        {"primary": "contract-a", "backup": "contract-b"},
    )
    router = MultiProviderMarketDataRouter(gateway, ["primary", "backup"])
    result = router.fetch_bars("ABC", "2026-11-02T09:00:00Z", "2026-11-02T11:00:00Z")
    assert result["provider"] == "backup"
    assert result["routing"]["selected_provider"] == "backup"
    assert result["routing"]["prior_provider_failures"] == [
        {"provider": "primary", "reason": "provider_fetch_failed"}
    ]
    assert result["live_execution"] is False


def test_router_fails_closed_when_all_providers_unavailable():
    router = MultiProviderMarketDataRouter(MarketDataGateway(), ["one", "two"])
    with pytest.raises(MarketDataUnavailable, match="all_configured_providers_unavailable"):
        router.fetch_bars("ABC", "2026-11-02T09:00:00Z", "2026-11-02T11:00:00Z")


def test_router_does_not_fail_over_on_invalid_market_data():
    gateway = MarketDataGateway(
        {"primary": Adapter([valid_bar() | {"high": 1}]), "backup": Adapter([valid_bar()])},
        {"primary": "contract-a", "backup": "contract-b"},
    )
    router = MultiProviderMarketDataRouter(gateway, ["primary", "backup"])
    with pytest.raises(ValueError, match="invalid_ohlc_high"):
        router.fetch_bars("ABC", "2026-11-02T09:00:00Z", "2026-11-02T11:00:00Z")


def test_router_rejects_ambiguous_provider_order():
    with pytest.raises(ValueError, match="duplicate_provider_in_order"):
        MultiProviderMarketDataRouter(MarketDataGateway(), ["one", "one"])
