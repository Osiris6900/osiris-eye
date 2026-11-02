import pytest

from osiris_api.market_gateway import MarketDataGateway, MarketDataUnavailable


class Adapter:
    name = "sandbox"
    mode = "sandbox"

    def fetch_bars(self, symbol, start, end):
        return []


def test_registry_reports_capabilities_without_secret_material():
    gateway = MarketDataGateway()
    gateway.register_adapter(
        "demo-feed", Adapter(),
        supported_asset_classes=["equity", "fx"],
        entitlement_reference="secret-store://entitlements/demo",
        credential_reference="secret-store://providers/demo",
    )
    payload = gateway.providers()
    provider = payload["providers"][0]
    assert provider["supported_asset_classes"] == ["equity", "fx"]
    assert provider["credential_reference_present"] is True
    assert provider["available"] is True
    assert "secret-store://" not in str(payload)


def test_registry_rejects_duplicate_provider():
    gateway = MarketDataGateway({"demo-feed": Adapter()}, {"demo-feed": "entitlement"})
    with pytest.raises(ValueError, match="provider_already_registered"):
        gateway.register_adapter(
            "demo-feed", Adapter(), supported_asset_classes=["equity"],
            entitlement_reference="entitlement",
        )


def test_registry_rejects_unknown_asset_class():
    gateway = MarketDataGateway()
    with pytest.raises(ValueError, match="unsupported_asset_class"):
        gateway.register_adapter(
            "demo-feed", Adapter(), supported_asset_classes=["meme-assets"],
            entitlement_reference="entitlement",
        )


def test_fetch_fails_closed_when_provider_does_not_cover_asset_class():
    gateway = MarketDataGateway()
    gateway.register_adapter(
        "equity-feed", Adapter(), supported_asset_classes=["equity"],
        entitlement_reference="operator-ticket-123",
    )
    with pytest.raises(MarketDataUnavailable, match="provider_asset_class_not_supported"):
        gateway.fetch_bars(
            "equity-feed", "ABC", "2026-11-02T09:00:00Z",
            "2026-11-02T10:00:00Z", asset_class="crypto",
        )


def test_live_provider_mode_remains_disabled():
    class LiveAdapter(Adapter):
        mode = "live"

    gateway = MarketDataGateway()
    gateway.register_adapter(
        "live-feed", LiveAdapter(), supported_asset_classes=["equity"],
        entitlement_reference="operator-ticket-123",
    )
    with pytest.raises(MarketDataUnavailable, match="live_provider_mode_disabled"):
        gateway.fetch_bars(
            "live-feed", "ABC", "2026-11-02T09:00:00Z",
            "2026-11-02T10:00:00Z", asset_class="equity",
        )


def test_public_provider_mode_is_read_only_and_supported():
    class PublicAdapter(Adapter):
        mode = "public"

        def fetch_bars(self, symbol, start, end):
            return [{
                "symbol": symbol,
                "event_time": "2026-11-02T09:00:00+00:00",
                "available_at": "2026-11-02T09:01:00+00:00",
                "open": 10, "high": 12, "low": 9, "close": 11, "volume": 5,
            }]

    gateway = MarketDataGateway()
    gateway.register_adapter(
        "public-feed", PublicAdapter(), supported_asset_classes=["crypto"],
        entitlement_reference="public-data:test",
    )
    result = gateway.fetch_bars(
        "public-feed", "BTC-USD", "2026-11-02T09:00:00Z",
        "2026-11-02T09:05:00Z", asset_class="crypto",
    )
    assert result["mode"] == "public"
    assert result["live_execution"] is False
    assert gateway.providers()["live_market_data_enabled"] is False
