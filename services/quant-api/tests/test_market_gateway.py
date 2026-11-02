import pytest

from osiris_api.market_gateway import MarketDataGateway, MarketDataUnavailable, normalize_bar


class FakeAdapter:
    name = "test-feed"
    mode = "sandbox"

    def __init__(self, bars):
        self.bars = bars

    def fetch_bars(self, symbol, start, end):
        return self.bars


def bar(**overrides):
    value = {
        "symbol": "ABC",
        "event_time": "2026-11-02T10:00:00Z",
        "available_at": "2026-11-02T10:00:01Z",
        "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100,
    }
    value.update(overrides)
    return value


def test_gateway_normalizes_sorts_and_returns_provenance():
    gateway = MarketDataGateway(
        {"test-feed": FakeAdapter([bar(), bar(
            event_time="2026-11-02T09:00:00Z",
            available_at="2026-11-02T09:00:01Z",
        )])},
        {"test-feed": "operator-contract-ref"},
    )
    result = gateway.fetch_bars(
        "test-feed", "abc", "2026-11-02T08:00:00Z", "2026-11-02T11:00:00Z"
    )
    assert result["symbol"] == "ABC"
    assert result["bars_returned"] == 2
    assert result["bars"][0]["event_time"].startswith("2026-11-02T09:00:00")
    assert result["live_execution"] is False
    assert gateway.providers()["providers"][0]["available"] is True


def test_gateway_fails_closed_without_adapter_or_entitlement():
    with pytest.raises(MarketDataUnavailable, match="not_configured"):
        MarketDataGateway().fetch_bars(
            "missing", "ABC", "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z"
        )
    gateway = MarketDataGateway({"test-feed": FakeAdapter([bar()])})
    with pytest.raises(MarketDataUnavailable, match="reference_missing"):
        gateway.fetch_bars(
            "test-feed", "ABC", "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z"
        )


def test_live_adapter_is_blocked_even_with_operator_reference():
    adapter = FakeAdapter([bar()])
    adapter.mode = "live"
    gateway = MarketDataGateway({"test-feed": adapter}, {"test-feed": "contract"})
    with pytest.raises(MarketDataUnavailable, match="live_provider_mode_disabled"):
        gateway.fetch_bars(
            "test-feed", "ABC", "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z"
        )


@pytest.mark.parametrize("overrides, error", [
    ({"symbol": "OTHER"}, "symbol_mismatch"),
    ({"available_at": "2026-11-02T09:59:00Z"}, "availability_before_event"),
    ({"high": 10.5}, "invalid_ohlc_high"),
    ({"volume": -1}, "volume_must_be_non_negative"),
    ({"close": float("nan")}, "invalid_close"),
    ({"event_time": "2026-11-02T10:00:00"}, "timestamp_must_be_iso8601"),
])
def test_normalizer_rejects_bad_market_bars(overrides, error):
    with pytest.raises(ValueError, match=error):
        normalize_bar(bar(**overrides), "ABC")


def test_gateway_rejects_duplicate_timestamps_and_out_of_range_bars():
    duplicate = MarketDataGateway(
        {"test-feed": FakeAdapter([bar(), bar()])}, {"test-feed": "contract"}
    )
    with pytest.raises(ValueError, match="duplicate_bar_timestamp"):
        duplicate.fetch_bars(
            "test-feed", "ABC", "2026-11-02T09:00:00Z", "2026-11-02T11:00:00Z"
        )
    outside = MarketDataGateway(
        {"test-feed": FakeAdapter([bar()])}, {"test-feed": "contract"}
    )
    with pytest.raises(ValueError, match="bar_outside_requested_range"):
        outside.fetch_bars(
            "test-feed", "ABC", "2026-11-02T11:00:00Z", "2026-11-02T12:00:00Z"
        )
