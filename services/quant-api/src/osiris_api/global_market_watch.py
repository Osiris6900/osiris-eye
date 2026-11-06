"""Global live-market watch layer: the network observes markets itself.

Polls keyless public endpoints (no API key, no signup, no payment) — Binance
Spot 24h tickers for crypto, Frankfurter/ECB reference rates for FX, and
Stooq quotes for global indices — normalizes each quote into the signed
``osiris.observation.v1`` contract and feeds the authorized ingestion
pipeline (issue #87), so any node can watch world markets and share
observations over the mesh without anyone operating a vendor terminal.

Fail-closed: unreachable or malformed sources are recorded as errors; no
value is ever fabricated. Live trading remains disabled.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .data_store import ResearchStore
from .p2p_ingestion import IngestionRejection, ingest_p2p_observation

PROVIDER_BINANCE = "binance-public"
PROVIDER_FRANKFURTER = "frankfurter-ecb"
PROVIDER_STOOQ = "stooq-public"
PROVIDER_COINBASE = "coinbase-public"
PROVIDER_KRAKEN = "kraken-public"
PROVIDER_BITSTAMP = "bitstamp-public"
PROVIDER_ERAPI = "erapi-public"

DEFAULT_PROVIDERS = frozenset({
    PROVIDER_BINANCE, PROVIDER_FRANKFURTER, PROVIDER_STOOQ,
    PROVIDER_COINBASE, PROVIDER_KRAKEN, PROVIDER_BITSTAMP, PROVIDER_ERAPI,
})

#: Keyless default watchlist. Operators can extend it; every source here
#: needs no account, key or payment.
#:
#: Crypto instruments are canonical USD-quoted symbols ("BTC", "ETH",
#: "SOL", "XRP") shared across Coinbase, Kraken and Bitstamp — so the
#: mesh consensus (#115) gets genuinely independent sources per
#: instrument. Binance reports its own USDT pairs (BTCUSDT …) which stay
#: separate instruments; every payload records the actual pair honestly.
DEFAULT_WATCHLIST: dict[str, tuple[str, ...]] = {
    PROVIDER_BINANCE: ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"),
    PROVIDER_FRANKFURTER: ("EUR", "GBP", "JPY", "INR", "CNY", "AUD", "CAD", "CHF"),
    PROVIDER_STOOQ: ("^spx", "^ndq", "^dji", "^nkx"),
    PROVIDER_COINBASE: ("BTC", "ETH", "SOL", "XRP"),
    PROVIDER_KRAKEN: ("BTC", "ETH", "SOL", "XRP"),
    PROVIDER_BITSTAMP: ("BTC", "ETH", "SOL", "XRP"),
    PROVIDER_ERAPI: ("EUR", "GBP", "JPY", "INR", "CNY", "AUD", "CAD", "CHF"),
}

#: Canonical crypto symbols supported by the USD-quoted exchanges.
CANONICAL_CRYPTO = frozenset({"BTC", "ETH", "SOL", "XRP"})

#: Kraken result-key candidates per canonical symbol (Kraken decorates
#: pair names, e.g. BTC -> XXBTZUSD).
_KRAKEN_RESULT_KEYS: dict[str, tuple[str, ...]] = {
    "BTC": ("XXBTZUSD", "XBTUSD"),
    "ETH": ("XETHZUSD", "ETHUSD"),
    "SOL": ("SOLUSD",),
    "XRP": ("XXRPZUSD", "XRPUSD"),
}
_KRAKEN_REQUEST_PAIRS: dict[str, str] = {
    "BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD", "XRP": "XRPUSD",
}

MAX_SYMBOL_LENGTH = 32
MAX_RESPONSE_BYTES = 1_048_576
MAX_QUOTES_PER_SOURCE = 500

_BINANCE_SYMBOL = re.compile(r"[A-Z0-9]{5,20}")
_FX_SYMBOL = re.compile(r"[A-Z]{3}")
_STOOQ_SYMBOL = re.compile(r"[a-z0-9.^_-]{1,24}")

Transport = Callable[[str, float], bytes]


def _http_get(url: str, timeout: float) -> bytes:
    request = Request(url, headers={
        "Accept": "application/json,text/csv",
        "User-Agent": "Osiris-Eye/0.3 global-market-watch",
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("watch_source_unavailable") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("watch_response_too_large")
    return body


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _finite(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid_quote_value") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError("invalid_quote_value")
    return number


def _observation(instrument: str, provider: str, quote: dict[str, Any], now: datetime) -> dict[str, Any]:
    payload = json.dumps(quote, allow_nan=False, separators=(",", ":"), sort_keys=True)
    return {
        "schema": "osiris.observation.v1",
        "instrument": instrument,
        "observed_at": now.isoformat(),
        "provider": provider,
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


def _fetch_binance(symbols: tuple[str, ...], transport: Transport, timeout: float,
                   now: datetime) -> list[dict[str, Any]]:
    observations = []
    for symbol in symbols:
        product = symbol.strip().upper()
        if not _BINANCE_SYMBOL.fullmatch(product):
            raise ValueError("invalid_binance_symbol")
        body = transport(
            f"https://api.binance.com/api/v3/ticker/24hr?symbol={product}", timeout
        )
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_binance_ticker_response") from exc
        if not isinstance(data, dict) or data.get("symbol") != product:
            raise ValueError("invalid_binance_ticker_response")
        quote = {
            "last": _finite(data.get("lastPrice")),
            "change_pct_24h": float(data.get("priceChangePercent", "nan"))
            if str(data.get("priceChangePercent", "")).lstrip("-+.eE0123456789") == ""
            else None,
            "volume_24h": _finite(data.get("volume")) if data.get("volume") not in (None, "0", "0.0") else 0.0,
            "source": "binance_spot_24hr_ticker",
        }
        observations.append(_observation(product, PROVIDER_BINANCE, quote, now))
        if len(observations) > MAX_QUOTES_PER_SOURCE:
            raise ValueError("too_many_quotes")
    return observations


def _fetch_frankfurter(symbols: tuple[str, ...], transport: Transport, timeout: float,
                       now: datetime) -> list[dict[str, Any]]:
    currencies = sorted({symbol.strip().upper() for symbol in symbols})
    if not currencies or any(_FX_SYMBOL.fullmatch(code) is None for code in currencies):
        raise ValueError("invalid_fx_symbol")
    body = transport(
        "https://api.frankfurter.dev/v1/latest?base=USD&symbols=" + ",".join(currencies),
        timeout,
    )
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_frankfurter_response") from exc
    rates = data.get("rates") if isinstance(data, dict) else None
    if not isinstance(rates, dict) or not rates:
        raise ValueError("invalid_frankfurter_response")
    observations = []
    for code in currencies:
        if code not in rates:
            continue  # source may not cover every currency; skip, never invent
        quote = {
            "rate_per_usd": _finite(rates[code]),
            "reference_date": str(data.get("date", "")),
            "source": "frankfurter_ecb_reference",
        }
        observations.append(_observation(f"USD-{code}", PROVIDER_FRANKFURTER, quote, now))
        if len(observations) > MAX_QUOTES_PER_SOURCE:
            raise ValueError("too_many_quotes")
    return observations


def _fetch_stooq(symbols: tuple[str, ...], transport: Transport, timeout: float,
                 now: datetime) -> list[dict[str, Any]]:
    tickers = [symbol.strip().lower() for symbol in symbols]
    if not tickers or any(_STOOQ_SYMBOL.fullmatch(ticker) is None for ticker in tickers):
        raise ValueError("invalid_stooq_symbol")
    body = transport(
        "https://stooq.com/q/l/?s=" + ",".join(tickers) + "&f=sd2t2ohlcv&h&e=csv",
        timeout,
    )
    try:
        reader = csv.DictReader(io.StringIO(body.decode("utf-8")))
        if not reader.fieldnames or not {"Symbol", "Date", "Time", "Open", "High", "Low", "Close", "Volume"}.issubset(set(reader.fieldnames)):
            raise ValueError("invalid_stooq_quote_csv")
        observations = []
        for row in reader:
            ticker = str(row.get("Symbol", "")).strip().lower()
            close = str(row.get("Close", "")).strip()
            if not ticker or close in ("", "N/D"):
                continue  # source has no quote for this symbol; skip, never invent
            quote = {
                "close": _finite(close),
                "open": _finite(row["Open"]) if str(row.get("Open", "")).strip() not in ("", "N/D") else None,
                "session_date": str(row.get("Date", "")).strip(),
                "session_time": str(row.get("Time", "")).strip(),
                "source": "stooq_delayed_quote",
            }
            observations.append(_observation(ticker.upper(), PROVIDER_STOOQ, quote, now))
            if len(observations) > MAX_QUOTES_PER_SOURCE:
                raise ValueError("too_many_quotes")
        return observations
    except (UnicodeDecodeError, KeyError, TypeError) as exc:
        raise ValueError("invalid_stooq_quote_csv") from exc


def _canonical_crypto(symbol: str) -> str:
    product = symbol.strip().upper()
    if product not in CANONICAL_CRYPTO:
        raise ValueError("invalid_canonical_crypto_symbol")
    return product


def _fetch_coinbase(symbols: tuple[str, ...], transport: Transport, timeout: float,
                    now: datetime) -> list[dict[str, Any]]:
    observations = []
    for symbol in symbols:
        product = _canonical_crypto(symbol)
        body = transport(
            f"https://api.exchange.coinbase.com/products/{product}-USD/ticker", timeout
        )
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_coinbase_ticker_response") from exc
        if not isinstance(data, dict) or "price" not in data:
            raise ValueError("invalid_coinbase_ticker_response")
        quote = {
            "last": _finite(data["price"]),
            "pair": f"{product}-USD",
            "quote_time": str(data.get("time", ""))[:40],
            "source": "coinbase_exchange_ticker",
        }
        observations.append(_observation(product, PROVIDER_COINBASE, quote, now))
        if len(observations) > MAX_QUOTES_PER_SOURCE:
            raise ValueError("too_many_quotes")
    return observations


def _fetch_kraken(symbols: tuple[str, ...], transport: Transport, timeout: float,
                  now: datetime) -> list[dict[str, Any]]:
    products = [_canonical_crypto(symbol) for symbol in symbols]
    pairs = [_KRAKEN_REQUEST_PAIRS[product] for product in products]
    body = transport(
        "https://api.kraken.com/0/public/Ticker?pair=" + ",".join(pairs), timeout
    )
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_kraken_ticker_response") from exc
    if not isinstance(data, dict) or not isinstance(data.get("result"), dict):
        raise ValueError("invalid_kraken_ticker_response")
    if data.get("error"):
        raise ValueError("invalid_kraken_ticker_response")
    result = data["result"]
    observations = []
    for product, request_pair in zip(products, pairs):
        entry = None
        for candidate in _KRAKEN_RESULT_KEYS[product] + (request_pair,):
            if candidate in result:
                entry = result[candidate]
                break
        if not isinstance(entry, dict):
            continue  # source did not return this pair; skip, never invent
        close = entry.get("c")
        if not isinstance(close, list) or not close:
            continue
        quote = {
            "last": _finite(close[0]),
            "pair": request_pair,
            "source": "kraken_public_ticker",
        }
        observations.append(_observation(product, PROVIDER_KRAKEN, quote, now))
        if len(observations) > MAX_QUOTES_PER_SOURCE:
            raise ValueError("too_many_quotes")
    return observations


def _fetch_bitstamp(symbols: tuple[str, ...], transport: Transport, timeout: float,
                    now: datetime) -> list[dict[str, Any]]:
    observations = []
    for symbol in symbols:
        product = _canonical_crypto(symbol)
        pair = f"{product.lower()}usd"
        body = transport(f"https://www.bitstamp.net/api/v2/ticker/{pair}/", timeout)
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_bitstamp_ticker_response") from exc
        if not isinstance(data, dict) or "last" not in data:
            raise ValueError("invalid_bitstamp_ticker_response")
        quote = {
            "last": _finite(data["last"]),
            "pair": pair,
            "source": "bitstamp_v2_ticker",
        }
        observations.append(_observation(product, PROVIDER_BITSTAMP, quote, now))
        if len(observations) > MAX_QUOTES_PER_SOURCE:
            raise ValueError("too_many_quotes")
    return observations


def _fetch_erapi(symbols: tuple[str, ...], transport: Transport, timeout: float,
                 now: datetime) -> list[dict[str, Any]]:
    currencies = sorted({symbol.strip().upper() for symbol in symbols})
    if not currencies or any(_FX_SYMBOL.fullmatch(code) is None for code in currencies):
        raise ValueError("invalid_fx_symbol")
    body = transport("https://open.er-api.com/v6/latest/USD", timeout)
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_erapi_response") from exc
    rates = data.get("rates") if isinstance(data, dict) else None
    if not isinstance(rates, dict) or not rates or data.get("result") != "success":
        raise ValueError("invalid_erapi_response")
    observations = []
    for code in currencies:
        if code not in rates:
            continue  # source may not cover every currency; skip, never invent
        quote = {
            "rate_per_usd": _finite(rates[code]),
            "source": "open_er_api_latest",
        }
        observations.append(_observation(f"USD-{code}", PROVIDER_ERAPI, quote, now))
        if len(observations) > MAX_QUOTES_PER_SOURCE:
            raise ValueError("too_many_quotes")
    return observations


_SOURCE_FETCHERS = {
    PROVIDER_BINANCE: _fetch_binance,
    PROVIDER_FRANKFURTER: _fetch_frankfurter,
    PROVIDER_STOOQ: _fetch_stooq,
    PROVIDER_COINBASE: _fetch_coinbase,
    PROVIDER_KRAKEN: _fetch_kraken,
    PROVIDER_BITSTAMP: _fetch_bitstamp,
    PROVIDER_ERAPI: _fetch_erapi,
}


def collect_market_observations(
    *,
    watchlist: dict[str, tuple[str, ...]] | None = None,
    transport: Transport = _http_get,
    timeout_seconds: float = 8.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch live quotes from every keyless source; errors are collected,
    never fatal, and no value is ever fabricated."""
    if not 1 <= timeout_seconds <= 30:
        raise ValueError("invalid_http_timeout")
    watchlist = watchlist or DEFAULT_WATCHLIST
    unknown = set(watchlist) - set(_SOURCE_FETCHERS)
    if unknown:
        raise ValueError("unknown_watch_source:" + ",".join(sorted(unknown)))

    current = now or _now_utc()
    observations: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for provider, symbols in sorted(watchlist.items()):
        try:
            observations.extend(
                _SOURCE_FETCHERS[provider](tuple(symbols), transport, timeout_seconds, current)
            )
        except (RuntimeError, ValueError) as exc:
            errors.append({"source": provider, "error": str(exc)})
    return {
        "observations": observations,
        "errors": errors,
        "collected_at": current.isoformat(),
        "live_execution": False,
    }


def watch_and_ingest(
    store: ResearchStore,
    *,
    allowed_providers: set[str] | frozenset[str] | None = None,
    watchlist: dict[str, tuple[str, ...]] | None = None,
    transport: Transport = _http_get,
    timeout_seconds: float = 8.0,
    max_age_seconds: float = 300.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One watch cycle: fetch world markets, then run every quote through
    the fail-closed ingestion gates into the research store.

    Duplicate quotes (price unchanged between cycles) are counted and
    skipped, not stored twice.
    """
    current = now or _now_utc()
    collected = collect_market_observations(
        watchlist=watchlist, transport=transport,
        timeout_seconds=timeout_seconds, now=current,
    )
    providers = set(allowed_providers) if allowed_providers is not None else set(DEFAULT_PROVIDERS)

    ingested = 0
    duplicates = 0
    rejected: list[dict[str, str]] = []
    for observation in collected["observations"]:
        try:
            ingest_p2p_observation(
                store, observation,
                allowed_providers=providers,
                max_age_seconds=max_age_seconds,
                now=current,
            )
            ingested += 1
        except IngestionRejection as exc:
            reason = str(exc)
            if reason.startswith("store_rejected:duplicate"):
                duplicates += 1
            else:
                rejected.append({
                    "instrument": str(observation.get("instrument", "?")),
                    "reason": reason,
                })

    return {
        "cycle": "global_market_watch",
        "fetched": len(collected["observations"]),
        "ingested": ingested,
        "duplicates_skipped": duplicates,
        "rejected": rejected,
        "source_errors": collected["errors"],
        "collected_at": collected["collected_at"],
        "live_execution": False,
        "broker_order_submitted": False,
    }
