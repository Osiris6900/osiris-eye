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
    with patch("osiris_api.public_market_data._get", return_value=json.dumps({"bad": True}).encode()):
        with pytest.raises(ValueError, match="invalid_coinbase_candles_response"):
            adapter.fetch_bars(
                "BTC-USD", "2026-11-02T00:00:00Z", "2026-11-02T00:05:00Z"
            )


from osiris_api.public_market_data import BinancePublicMarketDataAdapter, StooqDailyMarketDataAdapter


def test_binance_public_adapter_maps_spot_klines():
    timestamp = int(datetime(2026, 11, 2, tzinfo=timezone.utc).timestamp() * 1000)
    row = [timestamp, "100", "110", "95", "105", "12.5", timestamp + 59999,
           "1300", 10, "6", "600", "0"]
    with patch("osiris_api.public_market_data._get", return_value=json.dumps([row]).encode()):
        bars = BinancePublicMarketDataAdapter().fetch_bars(
            "BTCUSDT", "2026-11-02T00:00:00Z", "2026-11-02T00:05:00Z"
        )
    assert bars[0]["symbol"] == "BTCUSDT"
    assert bars[0]["open"] == 100
    assert bars[0]["high"] == 110
    assert bars[0]["low"] == 95
    assert bars[0]["close"] == 105
    assert bars[0]["volume"] == 12.5


def test_binance_rejects_invalid_symbol_and_unsupported_interval():
    with pytest.raises(ValueError, match="invalid_binance_symbol"):
        BinancePublicMarketDataAdapter().fetch_bars(
            "BTC/USDT", "2026-11-02T00:00:00Z", "2026-11-02T00:05:00Z"
        )
    with pytest.raises(ValueError, match="unsupported_binance_interval"):
        BinancePublicMarketDataAdapter(interval="3m")


def test_stooq_daily_adapter_parses_csv_as_daily_bars():
    payload = b"Date,Open,High,Low,Close,Volume\\n2026-11-02,10,12,9,11,1000\\n"
    with patch("osiris_api.public_market_data._get", return_value=payload):
        bars = StooqDailyMarketDataAdapter().fetch_bars(
            "aapl.us", "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z"
        )
    assert bars[0]["symbol"] == "AAPL.US"
    assert bars[0]["close"] == 11
    assert bars[0]["volume"] == 1000


def test_stooq_rejects_invalid_symbol():
    with pytest.raises(ValueError, match="invalid_stooq_symbol"):
        StooqDailyMarketDataAdapter().fetch_bars(
            "../../etc/passwd", "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z"
        )
