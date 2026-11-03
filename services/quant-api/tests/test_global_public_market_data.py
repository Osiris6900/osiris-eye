from io import BytesIO
from urllib.parse import urlparse, parse_qs

import pytest

from osiris_api.global_public_market_data import (
    BinancePublicSpotAdapter, StooqDailyEquityAdapter,
)


class Response:
    def __init__(self, body):
        self.body = body
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return self.body


def test_binance_public_adapter_normalizes_klines(monkeypatch):
    def fake_urlopen(request, timeout):
        assert "api.binance.com/api/v3/klines" in request.full_url
        return Response(b'[[1793613600000,"100","105","99","103","12"]]')
    monkeypatch.setattr("osiris_api.global_public_market_data.urlopen", fake_urlopen)
    bars = BinancePublicSpotAdapter().fetch_bars(
        "BTCUSDT", "2026-11-02T09:59:00Z", "2026-11-02T10:01:00Z"
    )
    assert bars[0]["symbol"] == "BTCUSDT"
    assert bars[0]["close"] == 103
    assert bars[0]["volume"] == 12


def test_binance_rejects_separated_symbol():
    with pytest.raises(ValueError, match="binance_symbol_must_be_exchange_pair_without_separator"):
        BinancePublicSpotAdapter().fetch_bars(
            "BTC-USDT", "2026-11-02T09:59:00Z", "2026-11-02T10:01:00Z"
        )


def test_stooq_daily_adapter_parses_market_suffixed_ticker(monkeypatch):
    def fake_urlopen(request, timeout):
        query = parse_qs(urlparse(request.full_url).query)
        assert query["s"] == ["aapl.us"]
        return Response(b"Date,Open,High,Low,Close,Volume\n2026-11-02,100,105,99,103,1200\n")
    monkeypatch.setattr("osiris_api.global_public_market_data.urlopen", fake_urlopen)
    bars = StooqDailyEquityAdapter().fetch_bars(
        "AAPL.US", "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z"
    )
    assert bars[0]["symbol"] == "AAPL.US"
    assert bars[0]["close"] == 103
    assert bars[0]["volume"] == 1200


def test_stooq_requires_explicit_market_suffix():
    with pytest.raises(ValueError, match="stooq_symbol_requires_market_suffix"):
        StooqDailyEquityAdapter().fetch_bars(
            "AAPL", "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z"
        )
