"""Unauthenticated, read-only public market-data adapters.

No adapter in this module submits orders or requires broker credentials.
"""
from __future__ import annotations

from datetime import datetime, timezone
import csv
import io
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from typing import Any

_ALLOWED_GRANULARITIES = frozenset({60, 300, 900, 3600, 21600, 86400})


def _time_range(start: str, end: str) -> tuple[datetime, datetime]:
    try:
        first = datetime.fromisoformat(start.replace("Z", "+00:00"))
        last = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_market_data_time_range") from exc
    if first.tzinfo is None or last.tzinfo is None or last <= first:
        raise ValueError("invalid_market_data_time_range")
    return first.astimezone(timezone.utc), last.astimezone(timezone.utc)


def _get(url: str, *, timeout: float = 8.0) -> bytes:
    request = Request(url, headers={
        "Accept": "application/json,text/csv",
        "User-Agent": "Osiris-Eye/0.2 public-market-data",
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("public_market_data_upstream_unavailable") from exc


def _bar(symbol: str, timestamp: datetime, available_at: str,
         open_price: Any, high: Any, low: Any, close: Any, volume: Any) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "event_time": timestamp.astimezone(timezone.utc).isoformat(),
        "available_at": available_at,
        "open": float(open_price), "high": float(high), "low": float(low),
        "close": float(close), "volume": float(volume),
    }


class CoinbasePublicMarketDataAdapter:
    """Fetch public Coinbase Exchange OHLCV candles without API credentials."""

    name = "Coinbase Exchange public market data"
    mode = "public"

    def __init__(self, *, granularity: int = 60, timeout_seconds: float = 8.0) -> None:
        if granularity not in _ALLOWED_GRANULARITIES:
            raise ValueError("unsupported_candle_granularity")
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("invalid_http_timeout")
        self.granularity, self.timeout_seconds = granularity, timeout_seconds

    def fetch_bars(self, symbol: str, start: str, end: str) -> list[dict[str, Any]]:
        product_id = symbol.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{2,15}-[A-Z0-9]{2,15}", product_id):
            raise ValueError("coinbase_symbol_must_be_BASE-QUOTE")
        first, last = _time_range(start, end)
        if (last - first).total_seconds() > self.granularity * 300:
            raise ValueError("requested_range_exceeds_300_candles")
        query = urlencode({
            "granularity": self.granularity,
            "start": first.isoformat(), "end": last.isoformat(),
        })
        payload = json.loads(_get(
            f"https://api.exchange.coinbase.com/products/{product_id}/candles?{query}",
            timeout=self.timeout_seconds,
        ).decode("utf-8"))
        if not isinstance(payload, list) or len(payload) > 300:
            raise ValueError("invalid_coinbase_candles_response")
        available = datetime.now(timezone.utc).isoformat()
        bars = []
        for candle in payload:
            if not isinstance(candle, list) or len(candle) != 6:
                raise ValueError("invalid_coinbase_candle")
            timestamp, low, high, open_price, close, volume = candle
            bars.append(_bar(product_id, datetime.fromtimestamp(timestamp, timezone.utc),
                             available, open_price, high, low, close, volume))
        return bars


class BinancePublicMarketDataAdapter:
    """Fetch public Binance Spot klines; no API key or trading permission is used."""

    name = "Binance public spot market data"
    mode = "public"

    def __init__(self, *, interval: str = "1m", timeout_seconds: float = 8.0) -> None:
        if interval not in {"1m", "5m", "15m", "1h", "4h", "1d"}:
            raise ValueError("unsupported_binance_interval")
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("invalid_http_timeout")
        self.interval, self.timeout_seconds = interval, timeout_seconds

    def fetch_bars(self, symbol: str, start: str, end: str) -> list[dict[str, Any]]:
        product = symbol.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{5,20}", product):
            raise ValueError("invalid_binance_symbol")
        first, last = _time_range(start, end)
        if (last - first).total_seconds() > 1000 * 86400:
            raise ValueError("requested_range_exceeds_binance_limit")
        query = urlencode({
            "symbol": product, "interval": self.interval,
            "startTime": int(first.timestamp() * 1000),
            "endTime": int(last.timestamp() * 1000), "limit": 1000,
        })
        payload = json.loads(_get(
            f"https://api.binance.com/api/v3/klines?{query}",
            timeout=self.timeout_seconds,
        ).decode("utf-8"))
        if not isinstance(payload, list) or len(payload) > 1000:
            raise ValueError("invalid_binance_klines_response")
        available = datetime.now(timezone.utc).isoformat()
        return [
            _bar(product, datetime.fromtimestamp(row[0] / 1000, timezone.utc),
                 available, row[1], row[2], row[3], row[4], row[5])
            for row in payload if isinstance(row, list) and len(row) >= 6
        ]


class StooqDailyMarketDataAdapter:
    """Fetch best-effort daily historical OHLCV CSV from Stooq; not a real-time feed."""

    name = "Stooq daily historical market data"
    mode = "public"

    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("invalid_http_timeout")
        self.timeout_seconds = timeout_seconds

    def fetch_bars(self, symbol: str, start: str, end: str) -> list[dict[str, Any]]:
        ticker = symbol.strip().lower()
        if not re.fullmatch(r"[a-z0-9.^_-]{1,24}", ticker):
            raise ValueError("invalid_stooq_symbol")
        first, last = _time_range(start, end)
        query = urlencode({
            "s": ticker, "d1": first.strftime("%Y%m%d"),
            "d2": last.strftime("%Y%m%d"), "i": "d",
        })
        raw = _get(f"https://stooq.com/q/d/l/?{query}", timeout=self.timeout_seconds)
        try:
            reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
            if not reader.fieldnames or not {"Date", "Open", "High", "Low", "Close", "Volume"}.issubset(reader.fieldnames):
                raise ValueError("invalid_stooq_csv_header")
            available = datetime.now(timezone.utc).isoformat()
            bars = []
            for row in reader:
                day = datetime.strptime(row["Date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                bars.append(_bar(ticker.upper(), day, available, row["Open"], row["High"],
                                 row["Low"], row["Close"], row["Volume"]))
                if len(bars) > 5000:
                    raise ValueError("stooq_response_exceeds_limit")
            return bars
        except (UnicodeDecodeError, KeyError, TypeError) as exc:
            raise ValueError("invalid_stooq_csv_response") from exc
