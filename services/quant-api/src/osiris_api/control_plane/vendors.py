"""Read-only vendor bar adapters with bounded HTTP and explicit entitlement references."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..market_gateway import normalize_bar, parse_market_time


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpJson:
    def request(self, method: str, url: str, *, headers: dict | None = None, body=None):
        for key, value in (headers or {}).items():
            if (
                not isinstance(key, str)
                or not isinstance(value, str)
                or len(value) > 16384
                or not key.isascii()
                or not value.isascii()
                or any(c in key + value for c in "\r\n")
            ):
                raise ValueError("invalid_http_header")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.port == 0
        ):
            raise ValueError("https_origin_required")
        if method not in {"GET", "POST", "PATCH", "DELETE", "PUT"}:
            raise ValueError("unsupported_http_method")
        data = None if body is None else json.dumps(body, allow_nan=False).encode()
        if data is not None and len(data) > 1_048_576:
            raise ValueError("request_too_large")
        request = Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with build_opener(NoRedirect()).open(request, timeout=10) as response:
                raw = response.read(2_097_153)
                if len(raw) > 2_097_152:
                    raise ValueError("response_too_large")
                return {} if not raw else json.loads(raw)
        except (HTTPError, URLError, OSError, UnicodeError, ValueError):
            # Never include URLs, credential headers or upstream response bodies.
            raise ValueError("upstream_request_failed") from None


def number(value) -> float:
    if isinstance(value, bool):
        raise ValueError("invalid_provider_number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid_provider_number") from None
    if not math.isfinite(result):
        raise ValueError("invalid_provider_number")
    return result


class VendorBarAdapter:
    """Research-sandbox access to real upstream data; never an order adapter.

    Operator references are not independent proof of licensing. Historical
    availability is conservatively the actual retrieval time, not backdated.
    """

    mode = "sandbox"
    HOSTS = {
        "alpaca": "data.alpaca.markets",
        "oanda": "api-fxpractice.oanda.com",
        "polygon": "api.polygon.io",
        "twelvedata": "api.twelvedata.com",
    }

    def __init__(
        self,
        provider: str,
        credentials: dict[str, str],
        *,
        entitlement_reference: str,
        transport=None,
        clock=None,
    ):
        if (
            provider not in self.HOSTS
            or not isinstance(entitlement_reference, str)
            or not entitlement_reference.strip()
        ):
            raise ValueError("provider_and_entitlement_reference_required")
        required = ("key", "secret") if provider == "alpaca" else ("key",)
        if any(
            not isinstance(credentials.get(k), str)
            or not credentials[k]
            or any(c in credentials[k] for c in "\r\n")
            for k in required
        ):
            raise ValueError("provider_credentials_required")
        self.name, self.credentials = provider, dict(credentials)
        self.transport = transport or HttpJson()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def fetch_bars(self, symbol: str, start: str, end: str) -> list[dict]:
        start_dt, end_dt = parse_market_time(start), parse_market_time(end)
        if not isinstance(symbol, str) or not 1 <= len(symbol) <= 64 or end_dt <= start_dt:
            raise ValueError("invalid_bar_request")
        origin = "https://" + self.HOSTS[self.name]
        encoded = quote(symbol, safe="")
        headers = {}
        if self.name == "alpaca":
            headers = {
                "APCA-API-KEY-ID": self.credentials["key"],
                "APCA-API-SECRET-KEY": self.credentials["secret"],
            }
            url = (
                origin
                + f"/v2/stocks/{encoded}/bars?"
                + urlencode(
                    {"start": start, "end": end, "timeframe": "1Day", "limit": 10000, "sort": "asc"}
                )
            )
        elif self.name == "oanda":
            headers = {"Authorization": "Bearer " + self.credentials["key"]}
            url = (
                origin
                + f"/v3/instruments/{encoded}/candles?"
                + urlencode({"from": start, "to": end, "granularity": "D", "price": "M"})
            )
        elif self.name == "polygon":
            headers = {"Authorization": "Bearer " + self.credentials["key"]}
            url = (
                origin
                + f"/v2/aggs/ticker/{encoded}/range/1/day/{start_dt.date()}/{end_dt.date()}?adjusted=true&sort=asc&limit=50000"
            )
        else:
            headers = {"Authorization": "apikey " + self.credentials["key"]}
            url = (
                origin
                + "/time_series?"
                + urlencode(
                    {
                        "symbol": symbol,
                        "interval": "1day",
                        "start_date": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                        "end_date": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
                        "timezone": "UTC",
                        "outputsize": 5000,
                    }
                )
            )
        rows, seen_pages = [], set()
        while url:
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.hostname != self.HOSTS[self.name]
                or parsed.port not in (None, 443)
                or parsed.username
                or parsed.password
                or parsed.fragment
                or "apikey" in parsed.query.lower()
            ):
                raise ValueError("unsafe_provider_pagination")
            if url in seen_pages or len(seen_pages) >= 32:
                raise ValueError("pagination_limit_or_cycle")
            seen_pages.add(url)
            data = self.transport.request("GET", url, headers=headers)
            if not isinstance(data, dict):
                raise ValueError("invalid_vendor_response")
            key = {
                "alpaca": "bars",
                "oanda": "candles",
                "polygon": "results",
                "twelvedata": "values",
            }[self.name]
            batch = data.get(key)
            if not isinstance(batch, list) or len(batch) > 50000:
                raise ValueError("missing_vendor_bars")
            rows.extend(batch)
            if len(rows) > 50000:
                raise ValueError("too_many_vendor_bars")
            if self.name == "alpaca" and data.get("next_page_token"):
                token = data["next_page_token"]
                if not isinstance(token, str) or len(token) > 2048:
                    raise ValueError("invalid_page_token")
                url = (
                    origin
                    + f"/v2/stocks/{encoded}/bars?"
                    + urlencode(
                        {
                            "start": start,
                            "end": end,
                            "timeframe": "1Day",
                            "limit": 10000,
                            "sort": "asc",
                            "page_token": token,
                        }
                    )
                )
            elif self.name == "polygon":
                url = data.get("next_url")
                if url is not None and not isinstance(url, str):
                    raise ValueError("invalid_next_page")
            else:
                url = None
        received = self.clock().astimezone(timezone.utc)
        result, timestamps = [], set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("invalid_vendor_bar")
            if self.name == "oanda":
                if row.get("complete") is not True:
                    continue
                prices, stamp, volume = row.get("mid"), row.get("time"), row.get("volume")
            elif self.name == "twelvedata":
                prices, stamp, volume = row, row.get("datetime"), row.get("volume")
                if isinstance(stamp, str):
                    stamp = stamp.replace(" ", "T") + ("T00:00:00Z" if len(stamp) == 10 else "Z")
            else:
                prices, stamp, volume = row, row.get("t"), row.get("v")
                if self.name == "polygon":
                    stamp = datetime.fromtimestamp(number(stamp) / 1000, timezone.utc).isoformat()
            instant = parse_market_time(stamp)
            if instant > received:
                raise ValueError("future_vendor_bar")
            if not start_dt <= instant <= end_dt:
                continue
            if instant in timestamps:
                raise ValueError("duplicate_vendor_bar")
            timestamps.add(instant)
            if not isinstance(prices, dict):
                raise ValueError("missing_vendor_prices")
            fields = (
                ("open", "high", "low", "close")
                if self.name == "twelvedata"
                else ("o", "h", "l", "c")
            )
            bar = {
                "symbol": symbol,
                "event_time": instant.isoformat(),
                "available_at": received.isoformat(),
                "volume": number(volume),
                **{
                    field: number(prices.get(key))
                    for field, key in zip(("open", "high", "low", "close"), fields)
                },
            }
            result.append(normalize_bar(bar, symbol))
        return sorted(result, key=lambda row: row["event_time"])
