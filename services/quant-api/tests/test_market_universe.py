import pytest

from osiris_api.market_universe import ASSET_CLASSES, audit_instruments, normalize_instrument


def instrument(**overrides):
    value = {
        "asset_class": "equity", "venue": "NSE", "symbol": "reliance",
        "quote_currency": "inr", "timezone": "Asia/Kolkata",
        "session_type": "exchange", "tick_size": "0.05", "lot_size": 1,
    }
    value.update(overrides)
    return value


def test_global_asset_class_catalog_covers_requested_markets():
    ids = {item["id"] for item in ASSET_CLASSES}
    assert {"equity", "crypto", "forex", "future", "option", "commodity"} <= ids


def test_normalizes_instrument_metadata_and_identifier():
    result = normalize_instrument(instrument())
    assert result["instrument_id"] == "equity:NSE:RELIANCE"
    assert result["quote_currency"] == "INR"
    assert result["tick_size"] == "0.05"


@pytest.mark.parametrize("overrides,error", [
    ({"asset_class": "unknown"}, "unsupported_asset_class"),
    ({"timezone": "Not/A_Timezone"}, "invalid_timezone"),
    ({"tick_size": 0}, "invalid_tick_size"),
    ({"lot_size": True}, "invalid_lot_size"),
    ({"session_type": "random"}, "invalid_session_type"),
])
def test_rejects_invalid_instrument_metadata(overrides, error):
    with pytest.raises(ValueError, match=error):
        normalize_instrument(instrument(**overrides))


def test_batch_audit_rejects_duplicates_without_persisting():
    result = audit_instruments([instrument(), instrument()])
    assert result["accepted"] == 1
    assert result["rejected"] == 1
    assert result["errors"][0]["reason"] == "duplicate_instrument"
    assert result["catalog_persisted"] is False
    assert result["live_execution"] is False
