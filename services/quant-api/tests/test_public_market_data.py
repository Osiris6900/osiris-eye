from datetime import datetime, timezone
import json
from unittest.mock import patch

import pytest

from osiris_api.public_market_data import CoinbasePublicMarketDataAdapter


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_coinbase_public_adapter_normalizes_candle_shape():
    adapter = CoinbasePublicMarketDataAdapter()
    timestamp = int(datetime(2026, 11, 2, tzinfo=timezone.utc).timestamp())
    with patch("osiris_api.public_market_data.urlopen",
               return_value=FakeResponse([[timestamp, 99, 102, 100, 101, 12.5]])):
        bars = adapter.fetch_bars(
            "BTC-USD", "2026-11-02T00:00:00Z", "2026-11-02T00:05:00Z"
        )
    assert bars[0]["symbol"] == "BTC-USD"
    assert bars[0]["open"] == 100
    assert bars[0]["high"] == 102
    assert bars[0]["low"] == 99
    assert bars[0]["close"] == 101
    assert bars[0]["volume"] == 12.5
    assert bars[0]["available_at"]


def test_coinbase_adapter_rejects_invalid_product_and_large_range():
    adapter = CoinbasePublicMarketDataAdapter()
    with pytest.raises(ValueError, match="BASE-QUOTE"):
        adapter.fetch_bars("BTCUSD", "2026-11-02T00:00:00Z", "2026-11-02T00:01:00Z")
    with pytest.raises(ValueError, match="300_candles"):
        adapter.fetch_bars("BTC-USD", "2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z")


def test_coinbase_adapter_rejects_bad_upstream_payload():
    adapter = CoinbasePublicMarketDataAdapter()
    with patch("osiris_api.public_market_data.urlopen", return_value=FakeResponse({"bad": True})):
        with pytest.raises(ValueError, match="invalid_coinbase_candles_response"):
            adapter.fetch_bars(
                "BTC-USD", "2026-11-02T00:00:00Z", "2026-11-02T00:05:00Z"
            )
