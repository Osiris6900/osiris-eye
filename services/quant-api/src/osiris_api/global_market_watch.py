"""Global live-market watch layer: the network observes markets itself.

Polls keyless public endpoints (no API key, no signup, no payment) — Binance,
OKX and Bybit spot tickers, Coinbase/Kraken/Bitstamp USD pairs,
Frankfurter/ECB and open-er-api reference rates for FX, and Stooq quotes for
global indices — normalizes each quote into the signed
``osiris.observation.v1`` contract and feeds the authorized ingestion
pipeline (issue #87), so any node can watch world markets and share
observations over the mesh without anyone operating a vendor terminal.

Every fetch is timed: the node-to-source HTTP round-trip (milliseconds) is
attached to each quote payload and reported per source, so the mesh knows
which exchanges are *network-near* to which node. This is honest latency
telemetry from wherever the node runs — not physical exchange co-location,
which requires rented rack space no software can substitute.

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
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .data_store import ResearchStore
from .market_connectivity import classify_transport_failure
from .p2p_ingestion import IngestionRejection, ingest_p2p_observation
from .watch_coverage import coverage_report

PROVIDER_BINANCE = "binance-public"
PROVIDER_FRANKFURTER = "frankfurter-ecb"
PROVIDER_STOOQ = "stooq-public"
PROVIDER_COINBASE = "coinbase-public"
PROVIDER_KRAKEN = "kraken-public"
PROVIDER_BITSTAMP = "bitstamp-public"
PROVIDER_ERAPI = "erapi-public"
PROVIDER_OKX = "okx-public"
PROVIDER_BYBIT = "bybit-public"

DEFAULT_PROVIDERS = frozenset({
    PROVIDER_BINANCE, PROVIDER_FRANKFURTER, PROVIDER_STOOQ,
    PROVIDER_COINBASE, PROVIDER_KRAKEN, PROVIDER_BITSTAMP, PROVIDER_ERAPI,
    PROVIDER_OKX, PROVIDER_BYBIT,
})

#: Keyless default watchlist. Operators can extend it; every source here
#: needs no account, key or payment.
#:
#: Crypto instruments are canonical USD-quoted symbols ("BTC", "ETH",
#: "SOL", "XRP") shared across Coinbase, Kraken and Bitstamp — so the
#: mesh consensus (#115) gets genuinely independent sources per
#: instrument. USDT-quoted pairs (BTCUSDT …) are likewise shared across
#: Binance, OKX and Bybit — the same pair on three exchanges is one
#: instrument with three independent observers — while USDT pairs stay
#: separate from USD instruments honestly; every payload records the
#: actual pair and venue.
DEFAULT_WATCHLIST: dict[str, tuple[str, ...]] = {
    PROVIDER_BINANCE: ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"),
    PROVIDER_FRANKFURTER: ("EUR", "GBP", "JPY", "INR", "CNY", "AUD", "CAD", "CHF"),
    PROVIDER_STOOQ: ("^spx", "^ndq", "^dji", "^nkx"),
    PROVIDER_COINBASE: ("BTC", "ETH", "SOL", "XRP"),
    PROVIDER_KRAKEN: ("BTC", "ETH", "SOL", "XRP"),
    PROVIDER_BITSTAMP: ("BTC", "ETH", "SOL", "XRP"),
    PROVIDER_ERAPI: ("EUR", "GBP", "JPY", "INR", "CNY", "AUD", "CAD", "CHF"),
    PROVIDER_OKX: ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"),
    PROVIDER_BYBIT: ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"),
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

#: Payload-stamped latency is quantized to 5 ms buckets: the "which source
#: is network-near" signal lives at tens-to-hundreds of ms, and bucketing
#: keeps an unchanged market yielding the identical payload hash so the
#: ingestion dedupe (duplicates_skipped) still holds across cycles.
LATENCY_BUCKET_MS = 5.0

_BINANCE_SYMBOL = re.compile(r"[A-Z0-9]{5,20}")
_FX_SYMBOL = re.compile(r"[A-Z]{3}")
_STOOQ_SYMBOL = re.compile(r"[a-z0-9.^_-]{1,24}")
_USDT_PAIR_SYMBOL = re.compile(r"[A-Z0-9]{2,10}USDT")

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
        raise classify_transport_failure(exc) from exc
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


def _with_fetch_latency(observation: dict[str, Any], elapsed_ms: float) -> dict[str, Any]:
    """Stamp the node-to-source HTTP round-trip onto a finished observation.

    The latency becomes part of the signed payload (hash recomputed), so a
    gossiped observation carries the *fetching* node's distance to that
    source with it. The value is quantized to ``LATENCY_BUCKET_MS`` buckets:
    sub-bucket timing noise must not defeat payload-hash dedupe when the
    market itself has not moved, and the network-nearness signal lives far
    above that resolution.
    """
    quote = json.loads(observation["payload"])
    bucketed = round(elapsed_ms / LATENCY_BUCKET_MS) * LATENCY_BUCKET_MS
    quote["fetch_roundtrip_ms"] = round(bucketed, 3)
    payload = json.dumps(quote, allow_nan=False, separators=(",", ":"), sort_keys=True)
    return {
        **observation,
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


def _usdt_pair(symbol: str, error: str) -> str:
    product = symbol.strip().upper()
    if _USDT_PAIR_SYMBOL.fullmatch(product) is None:
        raise ValueError(error)
    return product


def _fetch_okx(symbols: tuple[str, ...], transport: Transport, timeout: float,
               now: datetime) -> list[dict[str, Any]]:
    # OKX v5 public market data: one batch request covers every spot pair,
    # keeping request count (and measured round-trips) per source at one.
    products = [_usdt_pair(symbol, "invalid_okx_symbol") for symbol in symbols]
    wanted = {f"{product[:-4]}-USDT": product for product in products}
    body = transport("https://www.okx.com/api/v5/market/tickers?instType=SPOT", timeout)
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_okx_ticker_response") from exc
    tickers = data.get("data") if isinstance(data, dict) else None
    if not isinstance(data, dict) or data.get("code") != "0" or not isinstance(tickers, list):
        raise ValueError("invalid_okx_ticker_response")
    observations = []
    for entry in tickers:
        if not isinstance(entry, dict):
            continue
        product = wanted.get(str(entry.get("instId", "")))
        if product is None:
            continue  # not on the watchlist; ignore
        quote = {
            "last": _finite(entry.get("last")),
            "pair": str(entry["instId"]),
            "volume_24h": _finite(entry.get("volCcy24h"))
            if entry.get("volCcy24h") not in (None, "", "0", "0.0") else 0.0,
            "source": "okx_v5_spot_tickers",
        }
        observations.append(_observation(product, PROVIDER_OKX, quote, now))
        if len(observations) > MAX_QUOTES_PER_SOURCE:
            raise ValueError("too_many_quotes")
    return observations


def _fetch_bybit(symbols: tuple[str, ...], transport: Transport, timeout: float,
                 now: datetime) -> list[dict[str, Any]]:
    # Bybit v5 public market data: one batch request per category.
    products = [_usdt_pair(symbol, "invalid_bybit_symbol") for symbol in symbols]
    wanted = set(products)
    body = transport("https://api.bybit.com/v5/market/tickers?category=spot", timeout)
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_bybit_ticker_response") from exc
    result = data.get("result") if isinstance(data, dict) else None
    tickers = result.get("list") if isinstance(result, dict) else None
    if not isinstance(data, dict) or data.get("retCode") != 0 or not isinstance(tickers, list):
        raise ValueError("invalid_bybit_ticker_response")
    observations = []
    for entry in tickers:
        if not isinstance(entry, dict):
            continue
        product = str(entry.get("symbol", ""))
        if product not in wanted:
            continue  # not on the watchlist; ignore
        quote = {
            "last": _finite(entry.get("lastPrice")),
            "pair": product,
            "volume_24h": _finite(entry.get("volume24h"))
            if entry.get("volume24h") not in (None, "", "0", "0.0") else 0.0,
            "source": "bybit_v5_spot_tickers",
        }
        observations.append(_observation(product, PROVIDER_BYBIT, quote, now))
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
    PROVIDER_OKX: _fetch_okx,
    PROVIDER_BYBIT: _fetch_bybit,
}


def collect_market_observations(
    *,
    watchlist: dict[str, tuple[str, ...]] | None = None,
    transport: Transport = _http_get,
    timeout_seconds: float = 8.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch live quotes from every keyless source; errors are collected,
    never fatal, and no value is ever fabricated.

    Each source fetch is timed with a monotonic clock; the round-trip in
    milliseconds is stamped into every quote payload (``fetch_roundtrip_ms``)
    and summarized in the ``latencies`` list — one entry per attempted
    source, successful or not. These are network measurements from this
    node's vantage point, not exchange co-location.
    """
    if not 1 <= timeout_seconds <= 30:
        raise ValueError("invalid_http_timeout")
    watchlist = DEFAULT_WATCHLIST if watchlist is None else watchlist
    unknown = set(watchlist) - set(_SOURCE_FETCHERS)
    if unknown:
        raise ValueError("unknown_watch_source:" + ",".join(sorted(unknown)))

    current = now or _now_utc()
    observations: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    latencies: list[dict[str, Any]] = []
    for provider, symbols in sorted(watchlist.items()):
        started = time.monotonic()
        try:
            fetched = _SOURCE_FETCHERS[provider](
                tuple(symbols), transport, timeout_seconds, current
            )
        except (RuntimeError, ValueError) as exc:
            latencies.append({
                "source": provider,
                "fetch_roundtrip_ms": round((time.monotonic() - started) * 1000.0, 3),
                "ok": False,
            })
            errors.append({"source": provider, "error": str(exc)})
            continue
        elapsed_ms = (time.monotonic() - started) * 1000.0
        latencies.append({
            "source": provider,
            "fetch_roundtrip_ms": round(elapsed_ms, 3),
            "quotes": len(fetched),
            "ok": True,
        })
        observations.extend(_with_fetch_latency(obs, elapsed_ms) for obs in fetched)
    result = {
        "observations": observations,
        "errors": errors,
        "latencies": latencies,
        "collected_at": current.isoformat(),
        "live_execution": False,
    }
    result["coverage"] = coverage_report(watchlist, result)
    return result


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
        "latencies": collected["latencies"],
        "coverage": collected["coverage"],
        "collected_at": collected["collected_at"],
        "live_execution": False,
        "broker_order_submitted": False,
    }
