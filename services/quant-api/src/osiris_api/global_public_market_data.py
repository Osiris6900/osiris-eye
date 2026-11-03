"""Read-only public connectors for crypto spot and global daily equities."""
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

_SYMBOL = re.compile(r"^[A-Z0-9._-]{1,32}$")
_BINANCE_INTERVALS = {"1m", "5m", "15m", "1h", "4h", "1d"}
_STOOQ_INTERVALS = {"d"}


def _range(start: str, end: str) -> tuple[datetime, datetime]:
    a = datetime.fromisoformat(start.replace("Z", "+00:00"))
    b = datetime.fromisoformat(end.replace("Z", "+00:00"))
    if a.tzinfo is None or b.tzinfo is None or b <= a:
        raise ValueError("invalid_market_data_time_range")
    return a.astimezone(timezone.utc), b.astimezone(timezone.utc)


class BinancePublicSpotAdapter:
    """Public Binance Spot klines; no API key and no order endpoints."""

    name = "Binance public spot market data"
    mode = "public"

    def __init__(self, *, interval: str = "1m", timeout_seconds: float = 8.0):
        if interval not in _BINANCE_INTERVALS:
            raise ValueError("unsupported_binance_interval")
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("invalid_http_timeout")
        self.interval, self.timeout_seconds = interval, timeout_seconds

    def fetch_bars(self, symbol: str, start: str, end: str) -> list[dict[str, Any]]:
        symbol = symbol.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{5,20}", symbol):
            raise ValueError("binance_symbol_must_be_exchange_pair_without_separator")
        start_dt, end_dt = _range(start, end)
        if (end_dt - start_dt).total_seconds() > 1000 * 86400:
            raise ValueError("requested_range_exceeds_binance_limit")
        query = urlencode({
            "symbol": symbol, "interval": self.interval,
            "startTime": int(start_dt.timestamp() * 1000),
            "endTime": int(end_dt.timestamp() * 1000), "limit": 1000,
        })
        request = Request("https://api.binance.com/api/v3/klines?" + query, headers={
            "Accept": "application/json", "User-Agent": "Osiris-Eye/0.1",
        })
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("binance_public_market_data_unavailable") from exc
        if not isinstance(payload, list) or len(payload) > 1000:
            raise ValueError("invalid_binance_klines_response")
        available_at = datetime.now(timezone.utc).isoformat()
        bars = []
        for row in payload:
            if not isinstance(row, list) or len(row) < 6:
                raise ValueError("invalid_binance_kline")
            try:
                event_time = datetime.fromtimestamp(int(row[0]) / 1000, timezone.utc).isoformat()
                values = [float(row[i]) for i in (1, 2, 3, 4, 5)]
            except (TypeError, ValueError, OverflowError, IndexError):
                raise ValueError("invalid_binance_kline_values") from None
            bars.append({
                "symbol": symbol, "event_time": event_time, "available_at": available_at,
                "open": values[0], "high": values[1], "low": values[2],
                "close": values[3], "volume": values[4],
            })
        return bars


class StooqDailyEquityAdapter:
    """Stooq daily CSV history. Symbol suffixes identify Stooq's listing market."""

    name = "Stooq public daily equity history"
    mode = "public"

    def __init__(self, *, timeout_seconds: float = 10.0):
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("invalid_http_timeout")
        self.timeout_seconds = timeout_seconds

    def fetch_bars(self, symbol: str, start: str, end: str) -> list[dict[str, Any]]:
        symbol = symbol.strip().lower()
        if not _SYMBOL.fullmatch(symbol) or "." not in symbol:
            raise ValueError("stooq_symbol_requires_market_suffix")
        start_dt, end_dt = _range(start, end)
        query = urlencode({
            "s": symbol, "i": "d",
            "d1": start_dt.strftime("%Y%m%d"), "d2": end_dt.strftime("%Y%m%d"),
        })
        request = Request("https://stooq.com/q/d/l/?" + query, headers={
            "Accept": "text/csv", "User-Agent": "Osiris-Eye/0.1",
        })
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                content = response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError) as exc:
            raise RuntimeError("stooq_public_market_data_unavailable") from exc
        reader = csv.DictReader(io.StringIO(content))
        required = {"Date", "Open", "High", "Low", "Close", "Volume"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError("invalid_stooq_csv_schema")
        available_at = datetime.now(timezone.utc).isoformat()
        bars = []
        for row in reader:
            try:
                day = datetime.strptime(row["Date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                values = [float(row[key]) for key in ("Open", "High", "Low", "Close", "Volume")]
            except (TypeError, ValueError, KeyError):
                raise ValueError("invalid_stooq_daily_row") from None
            bars.append({
                "symbol": symbol.upper(), "event_time": day.isoformat(),
                "available_at": available_at, "open": values[0], "high": values[1],
                "low": values[2], "close": values[3], "volume": values[4],
            })
        return bars
