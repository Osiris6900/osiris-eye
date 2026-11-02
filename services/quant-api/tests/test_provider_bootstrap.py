from osiris_api.provider_bootstrap import provider_readiness


def test_provider_catalog_covers_currently_implemented_market_families():
    result = provider_readiness({})
    classes = {
        asset_class
        for provider in result["providers"]
        for asset_class in provider["supported_asset_classes"]
    }
    assert {"equity", "crypto", "fx", "future", "option", "index"} <= classes
    # Do not advertise these families until a provider adapter actually supports them.
    assert "commodity" not in classes
    assert "fixed_income" not in classes


def test_provider_readiness_reports_missing_environment_names_only():
    result = provider_readiness({
        "OSIRIS_BINANCE_API_KEY": "never-return-this-secret",
        "OSIRIS_BINANCE_API_SECRET": "also-secret",
    })
    provider = next(item for item in result["providers"] if item["provider"] == "binance")
    assert provider["configured"] is True
    assert provider["connectivity_verified"] is False
    assert "never-return-this-secret" not in str(result)
    assert "also-secret" not in str(result)


def test_partial_credentials_do_not_mark_provider_configured():
    result = provider_readiness({"OSIRIS_OANDA_API_TOKEN": "token-value"})
    provider = next(item for item in result["providers"] if item["provider"] == "oanda")
    assert provider["configured"] is False
    assert provider["missing_environment_variables"] == ["OSIRIS_OANDA_ACCOUNT_ID"]


def test_catalog_never_enables_market_data_or_live_execution():
    result = provider_readiness({})
    assert result["connectivity_verified"] is False
    assert result["live_market_data_enabled"] is False
    assert result["live_execution"] is False
    assert all(item["market_data_enabled"] is False for item in result["providers"])
