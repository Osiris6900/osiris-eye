"""Unauthenticated, read-only public market-data adapters.

No adapter in this module submits orders or requires broker credentials.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import csv
import io
import json
import math
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from typing import Any

_ALLOWED_GRANULARITIES = frozenset({60, 300, 900, 3600, 21600, 86400})


def _time_range(start: str, end: str) -> tuple[datetime, datetime]:
    if not isinstance(start, str) or not isinstance(end, str):
        raise ValueError("invalid_market_data_time_range")
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
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        raise ValueError("invalid_market_data_timestamp")
    if any(isinstance(value, bool) for value in (open_price, high, low, close, volume)):
        raise ValueError("invalid_market_data_ohlcv")
    try:
        values = [float(value) for value in (open_price, high, low, close, volume)]
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid_market_data_ohlcv") from exc
    if not all(math.isfinite(value) for value in values):
        raise ValueError("invalid_market_data_ohlcv")
    open_value, high_value, low_value, close_value, volume_value = values
    if (min(open_value, high_value, low_value, close_value) <= 0
            or volume_value < 0
            or high_value < max(open_value, close_value, low_value)
            or low_value > min(open_value, close_value, high_value)):
        raise ValueError("inconsistent_market_data_ohlcv")
    return {
        "symbol": symbol,
        "event_time": timestamp.astimezone(timezone.utc).isoformat(),
        "available_at": available_at,
        "open": open_value, "high": high_value, "low": low_value,
        "close": close_value, "volume": volume_value,
    }


class CoinbasePublicMarketDataAdapter:
    """Fetch public Coinbase Exchange OHLCV candles without API credentials."""

    name = "Coinbase Exchange public market data"
    mode = "public"

    def __init__(self, *, granularity: int = 60, timeout_seconds: float = 8.0) -> None:
        if type(granularity) is not int or granularity not in _ALLOWED_GRANULARITIES:
            raise ValueError("unsupported_candle_granularity")
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not 1 <= timeout_seconds <= 30):
            raise ValueError("invalid_http_timeout")
        self.granularity, self.timeout_seconds = granularity, timeout_seconds

    def fetch_bars(self, symbol: str, start: str, end: str) -> list[dict[str, Any]]:
        if not isinstance(symbol, str):
            raise ValueError("coinbase_symbol_must_be_BASE-QUOTE")
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
        try:
            payload = json.loads(_get(
                f"https://api.exchange.coinbase.com/products/{product_id}/candles?{query}",
                timeout=self.timeout_seconds,
            ).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_coinbase_candles_response") from exc
        if not isinstance(payload, list) or len(payload) > 300:
            raise ValueError("invalid_coinbase_candles_response")
        retrieved_at = datetime.now(timezone.utc)
        available = retrieved_at.isoformat()
        bars = []
        seen = set()
        for candle in payload:
            if not isinstance(candle, list) or len(candle) != 6:
                raise ValueError("invalid_coinbase_candle")
            timestamp, low, high, open_price, close, volume = candle
            if isinstance(timestamp, bool) or not isinstance(timestamp, int):
                raise ValueError("invalid_coinbase_candle_timestamp")
            try:
                opened_at = datetime.fromtimestamp(timestamp, timezone.utc)
                closed_at = opened_at + timedelta(seconds=self.granularity)
            except (OverflowError, OSError, ValueError) as exc:
                raise ValueError("invalid_coinbase_candle_timestamp") from exc
            if timestamp % self.granularity:
                raise ValueError("invalid_coinbase_candle_timestamp")
            bar = _bar(product_id, opened_at, available, open_price, high, low, close, volume)
            # Coinbase documents that responses can contain candles preceding
            # start. Never let those observations enter the requested history.
            if opened_at < first or opened_at >= last:
                continue
            if timestamp in seen:
                raise ValueError("duplicate_coinbase_candle")
            seen.add(timestamp)
            bar.update({"candle_end_time": closed_at.isoformat(),
                        "is_closed": closed_at <= retrieved_at})
            bars.append(bar)
        return sorted(bars, key=lambda bar: bar["event_time"])


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
        interval_seconds = {
            "1m": 60, "5m": 300, "15m": 900,
            "1h": 3600, "4h": 14400, "1d": 86400,
        }[self.interval]
        if (last - first).total_seconds() > interval_seconds * 1000:
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
        bars = []
        for row in payload:
            if not isinstance(row, list) or len(row) != 12:
                raise ValueError("invalid_binance_kline")
            try:
                if isinstance(row[0], bool) or not isinstance(row[0], (int, float)):
                    raise ValueError("invalid_binance_kline_timestamp")
                timestamp = datetime.fromtimestamp(row[0] / 1000, timezone.utc)
                bar = _bar(product, timestamp, available, row[1], row[2],
                           row[3], row[4], row[5])
                close_time = row[6]
                if isinstance(close_time, bool) or not isinstance(close_time, (int, float)):
                    raise ValueError("invalid_binance_kline_timestamp")
                if close_time < row[0] or timestamp < first or timestamp >= last:
                    raise ValueError("binance_kline_outside_requested_range")
            except (OverflowError, OSError) as exc:
                raise ValueError("invalid_binance_kline_timestamp") from exc
            bars.append(bar)
        return bars


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
