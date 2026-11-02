"""Unauthenticated, read-only public market-data adapters.

This module deliberately supports public market data only. It never submits orders,
stores credentials, or enables live execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from typing import Any

_BASE_URL = "https://api.exchange.coinbase.com"
_PRODUCT_ID = re.compile(r"^[A-Z0-9]{2,15}-[A-Z0-9]{2,15}$")
_ALLOWED_GRANULARITIES = frozenset({60, 300, 900, 3600, 21600, 86400})


class CoinbasePublicMarketDataAdapter:
    """Fetch public Coinbase Exchange OHLCV candles without API credentials."""

    name = "Coinbase Exchange public market data"
    mode = "public"

    def __init__(self, *, granularity: int = 60, timeout_seconds: float = 8.0) -> None:
        if granularity not in _ALLOWED_GRANULARITIES:
            raise ValueError("unsupported_candle_granularity")
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("invalid_http_timeout")
        self.granularity = granularity
        self.timeout_seconds = timeout_seconds

    def fetch_bars(self, symbol: str, start: str, end: str) -> list[dict[str, Any]]:
        product_id = symbol.strip().upper()
        if not _PRODUCT_ID.fullmatch(product_id):
            raise ValueError("coinbase_symbol_must_be_BASE-QUOTE")
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        if start_dt.tzinfo is None or end_dt.tzinfo is None or end_dt <= start_dt:
            raise ValueError("invalid_market_data_time_range")
        if (end_dt - start_dt).total_seconds() > self.granularity * 300:
            raise ValueError("requested_range_exceeds_300_candles")
        query = urlencode({
            "granularity": self.granularity,
            "start": start_dt.astimezone(timezone.utc).isoformat(),
            "end": end_dt.astimezone(timezone.utc).isoformat(),
        })
        url = f"{_BASE_URL}/products/{product_id}/candles?{query}"
        request = Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "Osiris-Eye/0.1 public-market-data",
        })
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            # Do not leak upstream response bodies, request headers, or environment secrets.
            raise RuntimeError("coinbase_public_market_data_unavailable") from exc
        if not isinstance(payload, list) or len(payload) > 300:
            raise ValueError("invalid_coinbase_candles_response")
        available_at = datetime.now(timezone.utc).isoformat()
        bars = []
        for candle in payload:
            if not isinstance(candle, list) or len(candle) != 6:
                raise ValueError("invalid_coinbase_candle")
            timestamp, low, high, open_price, close, volume = candle
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
                raise ValueError("invalid_coinbase_candle_timestamp")
            event_time = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
            bars.append({
                "symbol": product_id,
                "event_time": event_time,
                "available_at": available_at,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            })
        return bars
