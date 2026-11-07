"""Stream watch: the exchanges speak first — public push feeds, honestly bound.

The polling watch walks up to each venue every cycle and asks. The stream
watch opens the venues' own **public** WebSocket feeds and listens: the
exchange itself pushes every tick the moment it happens. This is the
closest legal thing to "living inside" the exchange — the venue streams
its own market data to anyone, no key, no signup.

Honest edges, same as the rest of the watch layer:

- **Public feeds only.** Every byte arrives on endpoints the venues
  publish for everyone. No authentication is bypassed, no private channel
  is touched, nothing is disguised.
- **Push beats poll; neither beats co-location.** A pushed tick removes
  the poll interval and one round-trip from the path — that is the honest
  gain. Co-located systems still read the matching engine in microseconds;
  no claim is made beyond "fresher than our own polling".
- **Same gates, same contract.** Streamed quotes become
  ``osiris.observation.v1`` observations and pass the identical fail-closed
  ingestion pipeline (kill switch, allowlist, freshness, hash dedupe). A
  fast pipe is not a trusted pipe.
- **Bounded by design.** Sessions have a hard duration, per-venue message
  caps, per-instrument emission throttles, bounded reconnects, and an
  inactivity watchdog. A runaway stream cannot flood the store.

Live trading remains disabled.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .data_store import ResearchStore
from .global_market_watch import (
    LATENCY_BUCKET_MS,
    PROVIDER_BINANCE,
    PROVIDER_BYBIT,
    PROVIDER_COINBASE,
    PROVIDER_OKX,
    _finite,
    _now_utc,
    _observation,
)
from .p2p_ingestion import IngestionRejection, ingest_p2p_observation
from .watch_coverage import coverage_report

STREAM_PROVIDERS = frozenset({
    PROVIDER_BINANCE,
    PROVIDER_OKX,
    PROVIDER_BYBIT,
    PROVIDER_COINBASE,
})

#: Same shared instruments as the polling watch: USDT pairs observed by
#: three venues, USD pairs on Coinbase. Canonical names; venue products
#: are derived per adapter.
DEFAULT_STREAM_WATCHLIST: dict[str, tuple[str, ...]] = {
    PROVIDER_BINANCE: ("BTCUSDT", "ETHUSDT"),
    PROVIDER_BYBIT: ("BTCUSDT", "ETHUSDT"),
    PROVIDER_COINBASE: ("BTC", "ETH"),
    PROVIDER_OKX: ("BTCUSDT", "ETHUSDT"),
}

MIN_STREAM_SECONDS = 5.0
MAX_STREAM_SECONDS = 86_400.0
MIN_MESSAGES = 1
MAX_MESSAGES = 1_000_000
DEFAULT_MAX_MESSAGES = 10_000
MIN_EMIT_INTERVAL_SECONDS = 0.2
MAX_EMIT_INTERVAL_SECONDS = 60.0
DEFAULT_EMIT_INTERVAL_SECONDS = 1.0
MIN_INACTIVITY_SECONDS = 5.0
MAX_INACTIVITY_SECONDS = 300.0
DEFAULT_INACTIVITY_SECONDS = 30.0
MAX_RECONNECTS_LIMIT = 10
DEFAULT_MAX_RECONNECTS = 3
_RECONNECT_BASE_SECONDS = 1.0
_RECONNECT_CAP_SECONDS = 30.0

_STREAM_URLS = {
    PROVIDER_BINANCE: "wss://stream.binance.com:9443/stream",
    PROVIDER_OKX: "wss://ws.okx.com:8443/ws/v5/public",
    PROVIDER_BYBIT: "wss://stream.bybit.com/v5/public/spot",
    PROVIDER_COINBASE: "wss://ws-feed.exchange.coinbase.com",
}


def _usdt_base(instrument: str) -> str:
    if not instrument.endswith("USDT") or len(instrument) <= 4:
        raise ValueError("unknown_stream_instrument:" + instrument)
    return instrument[:-4]


def _venue_product(provider: str, instrument: str) -> str:
    """Canonical watchlist instrument → the venue's own product label."""
    if provider == PROVIDER_BINANCE:
        return _usdt_base(instrument).lower() + "usdt"
    if provider == PROVIDER_OKX:
        return _usdt_base(instrument) + "-USDT"
    if provider == PROVIDER_BYBIT:
        _usdt_base(instrument)  # validate shape; Bybit uses the plain pair
        return instrument
    if provider == PROVIDER_COINBASE:
        if not instrument.isalpha() or not instrument.isupper():
            raise ValueError("unknown_stream_instrument:" + instrument)
        return instrument + "-USD"
    raise ValueError("unknown_stream_source:" + provider)


def _stream_url(provider: str, instruments: tuple[str, ...]) -> str:
    base = _STREAM_URLS[provider]
    if provider != PROVIDER_BINANCE:
        return base
    streams = "/".join(_venue_product(provider, name) + "@miniTicker" for name in instruments)
    return base + "?streams=" + streams


def _subscribe_frames(provider: str, instruments: tuple[str, ...]) -> list[str]:
    """Frames sent right after connect. Binance subscribes via the URL."""
    products = [_venue_product(provider, name) for name in instruments]
    if provider == PROVIDER_BINANCE:
        return []
    if provider == PROVIDER_OKX:
        return [json.dumps({
            "op": "subscribe",
            "args": [{"channel": "tickers", "instId": product} for product in products],
        })]
    if provider == PROVIDER_BYBIT:
        return [json.dumps({"op": "subscribe", "args": [f"tickers.{p}" for p in products]})]
    if provider == PROVIDER_COINBASE:
        return [json.dumps({"type": "subscribe", "product_ids": products, "channels": ["ticker"]})]
    raise ValueError("unknown_stream_source:" + provider)


def _optional_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _venue_ts_ms(value: Any) -> datetime | None:
    number = _optional_float(value)
    if number is None or not 1_000_000_000_000 <= number <= 10_000_000_000_000:
        return None  # ms sanity window: 2001-09 .. 2286-11
    return datetime.fromtimestamp(number / 1000.0, tz=timezone.utc)


def _iso_utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)  # py311 parses a trailing Z natively
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # A naive venue stamp is venue UTC — never reinterpret it as local time.
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_stream_message(provider: str, raw: bytes | str) -> list[dict[str, Any]]:
    """One venue frame → zero or more quotes; control frames → empty list.

    Subscription confirmations, heartbeats, pongs and error notices are
    not quotes: they return ``[]``. A frame that *claims* to carry a quote
    but has an unusable price raises ``ValueError`` — malformed market
    data is surfaced, never guessed at. Quote fields: ``instrument``
    (canonical), ``price``, optional ``volume_24h`` and optional
    ``venue_event_at`` (the venue's own timestamp, when it sends one).
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    text = raw.strip()
    if not text or text in {"pong", "ping"}:
        return []
    try:
        message = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_stream_frame") from exc
    if not isinstance(message, dict):
        return []

    if provider == PROVIDER_BINANCE:
        data = message.get("data")
        if not isinstance(data, dict) or data.get("e") != "24hrMiniTicker":
            return []
        symbol = str(data.get("s", "")).strip().upper()
        if not symbol:
            return []
        return [{
            "instrument": symbol,
            "price": _finite(data.get("c")),
            "volume_24h": _optional_float(data.get("v")),
            "venue_event_at": _venue_ts_ms(data.get("E")),
        }]

    if provider == PROVIDER_OKX:
        rows = message.get("data")
        if not isinstance(rows, list):
            return []  # subscribe confirmations and error notices
        quotes = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("instId"):
                continue
            quotes.append({
                "instrument": str(row["instId"]).replace("-", "").upper(),
                "price": _finite(row.get("last")),
                "volume_24h": _optional_float(row.get("volCcy24h")),
                "venue_event_at": _venue_ts_ms(row.get("ts")),
            })
        return quotes

    if provider == PROVIDER_BYBIT:
        topic = str(message.get("topic", ""))
        data = message.get("data")
        if not topic.startswith("tickers.") or not isinstance(data, dict):
            return []
        return [{
            "instrument": str(data.get("symbol", "")).strip().upper(),
            "price": _finite(data.get("lastPrice")),
            "volume_24h": _optional_float(data.get("volume24h")),
            "venue_event_at": _venue_ts_ms(message.get("ts")),
        }]

    if provider == PROVIDER_COINBASE:
        if message.get("type") != "ticker":
            return []
        product = str(message.get("product_id", "")).strip().upper()
        instrument = product.removesuffix("-USD")
        return [{
            "instrument": instrument,
            "price": _finite(message.get("price")),
            "volume_24h": _optional_float(message.get("volume_24h")),
            "venue_event_at": _iso_utc(message.get("time")),
        }]

    raise ValueError("unknown_stream_source:" + provider)


def _default_connect(url: str):
    """The real network door. Imported lazily so the module — and every
    test — can be imported without the websocket package installed.
    Install the optional extra: ``pip install osiris-quant-api[streams]``."""
    try:
        import websockets
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "stream_watch_requires_websockets_extra:pip install osiris-quant-api[streams]"
        ) from exc

    return websockets.connect(url, ping_interval=20, ping_timeout=20)


def _with_stream_stamp(
    observation: dict[str, Any],
    *,
    venue_event_at: datetime | None,
    received_at: datetime,
) -> dict[str, Any]:
    """Stamp push-feed provenance onto the signed payload (hash recomputed).

    ``venue_event_age_ms`` = receive wall-clock minus the venue's own event
    timestamp: how old the tick was when this node got it. It includes the
    node's clock skew — stated honestly in the payload. Quantized to
    ``LATENCY_BUCKET_MS`` like the HTTP round-trip stamp, so a quiet market
    still dedupes instead of flooding the store.
    """
    quote = json.loads(observation["payload"])
    quote["feed"] = "websocket_push"
    if venue_event_at is not None:
        age_ms = (received_at - venue_event_at).total_seconds() * 1000.0
        bucketed = round(age_ms / LATENCY_BUCKET_MS) * LATENCY_BUCKET_MS
        quote["venue_event_age_ms"] = round(bucketed, 3)
        quote["venue_ts_source"] = "venue"
    else:
        quote["venue_ts_source"] = "node"
    quote["clock_skew_note"] = "age_includes_node_clock_skew"
    payload = json.dumps(quote, allow_nan=False, separators=(",", ":"), sort_keys=True)
    return {
        **observation,
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


async def _run_venue(
    provider: str,
    instruments: tuple[str, ...],
    *,
    store: ResearchStore,
    allowed_providers: set[str] | frozenset[str],
    max_age_seconds: float,
    deadline: float,
    max_messages: int,
    emit_interval: float,
    inactivity_seconds: float,
    max_reconnects: int,
    connect: Callable[[str], Any],
    clock: Callable[[], float],
    sleep: Callable[[float], Any],
    now_fn: Callable[[], datetime],
) -> dict[str, Any]:
    """One venue, one bounded session: connect, subscribe, listen, ingest.

    Everything the session does lands in its stats — connections, frames,
    quotes, throttles, duplicates, rejections, reconnects, the last error.
    A venue that cannot stay connected ends its own session honestly; it
    never takes the others down.
    """
    stats: dict[str, Any] = {
        "provider": provider,
        "url": _stream_url(provider, instruments),
        "connections": 0,
        "frames": 0,
        "quotes_parsed": 0,
        "quotes_emitted": 0,
        "quotes_throttled": 0,
        "ingested": 0,
        "duplicates_skipped": 0,
        "rejected": 0,
        "parse_errors": 0,
        "reconnects": 0,
        "last_error": None,
        "stop_reason": "duration_elapsed",
    }
    emitted_observations: list[dict[str, Any]] = []
    last_emit: dict[str, float] = {}
    attempts_left = max_reconnects + 1

    while clock() < deadline and stats["frames"] < max_messages and attempts_left > 0:
        attempts_left -= 1
        try:
            async with connect(_stream_url(provider, instruments)) as socket:
                stats["connections"] += 1
                stats["last_error"] = None
                stats["stop_reason"] = "duration_elapsed"
                for frame in _subscribe_frames(provider, instruments):
                    await socket.send(frame)
                while clock() < deadline and stats["frames"] < max_messages:
                    try:
                        raw = await asyncio.wait_for(socket.recv(), inactivity_seconds)
                    except TimeoutError as exc:
                        stats["stop_reason"] = "inactivity_timeout"
                        raise ConnectionError("stream_inactive") from exc
                    stats["frames"] += 1
                    received = now_fn()
                    try:
                        quotes = parse_stream_message(provider, raw)
                    except ValueError:
                        stats["parse_errors"] += 1
                        continue
                    stats["quotes_parsed"] += len(quotes)
                    for quote in quotes:
                        instrument = quote["instrument"]
                        if instrument not in instruments:
                            continue  # not what we asked for: never adopt it
                        if clock() - last_emit.get(instrument, float("-inf")) < emit_interval:
                            stats["quotes_throttled"] += 1
                            continue
                        last_emit[instrument] = clock()
                        stats["quotes_emitted"] += 1
                        venue_event_at = quote.get("venue_event_at")
                        body = {"price": quote["price"]}
                        if quote.get("volume_24h") is not None:
                            body["volume_24h"] = quote["volume_24h"]
                        observation = _observation(
                            instrument, provider, body, venue_event_at or received
                        )
                        observation = _with_stream_stamp(
                            observation,
                            venue_event_at=venue_event_at,
                            received_at=received,
                        )
                        emitted_observations.append(observation)
                        try:
                            ingest_p2p_observation(
                                store,
                                observation,
                                allowed_providers=allowed_providers,
                                max_age_seconds=max_age_seconds,
                                now=received,
                            )
                            stats["ingested"] += 1
                        except IngestionRejection as exc:
                            if str(exc).startswith("store_rejected:duplicate"):
                                stats["duplicates_skipped"] += 1
                            else:
                                stats["rejected"] += 1
                if stats["frames"] >= max_messages:
                    stats["stop_reason"] = "frame_budget_spent"
        except Exception as exc:  # noqa: BLE001 — one venue's failure is contained
            stats["last_error"] = f"{type(exc).__name__}:{exc}"
            if attempts_left > 0 and clock() < deadline:
                stats["reconnects"] += 1
                backoff = min(
                    _RECONNECT_BASE_SECONDS * (2 ** stats["reconnects"] - 1),
                    _RECONNECT_CAP_SECONDS,
                )
                await sleep(min(backoff, max(0.0, deadline - clock())))

    if attempts_left <= 0 and stats["last_error"]:
        stats["stop_reason"] = "reconnects_exhausted"
    stats["emitted_observations"] = emitted_observations
    return stats


def run_stream_watch(
    store: ResearchStore,
    *,
    stream_seconds: float,
    watchlist: dict[str, tuple[str, ...]] | None = None,
    allowed_providers: set[str] | frozenset[str] | None = None,
    max_age_seconds: float = 300.0,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    emit_interval_seconds: float = DEFAULT_EMIT_INTERVAL_SECONDS,
    inactivity_seconds: float = DEFAULT_INACTIVITY_SECONDS,
    max_reconnects: int = DEFAULT_MAX_RECONNECTS,
    connect: Callable[[str], Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Any] | None = None,
    now_fn: Callable[[], datetime] = _now_utc,
) -> dict[str, Any]:
    """Bounded push-feed session across venues: the exchanges speak first.

    Runs every venue concurrently for ``stream_seconds``, feeding each
    throttled tick through the same fail-closed ingestion gates the
    polling watch uses. For truly continuous duty, call this in a loop
    under a supervisor — like the roaming loop, a session is deliberately
    finite. The ingestion kill switch gates every streamed quote exactly
    as it gates polled ones.
    """
    if not MIN_STREAM_SECONDS <= stream_seconds <= MAX_STREAM_SECONDS:
        raise ValueError("invalid_stream_seconds")
    if not MIN_MESSAGES <= max_messages <= MAX_MESSAGES:
        raise ValueError("invalid_max_messages")
    if not MIN_EMIT_INTERVAL_SECONDS <= emit_interval_seconds <= MAX_EMIT_INTERVAL_SECONDS:
        raise ValueError("invalid_emit_interval_seconds")
    if not MIN_INACTIVITY_SECONDS <= inactivity_seconds <= MAX_INACTIVITY_SECONDS:
        raise ValueError("invalid_inactivity_seconds")
    if not 0 <= max_reconnects <= MAX_RECONNECTS_LIMIT:
        raise ValueError("invalid_max_reconnects")
    watchlist = DEFAULT_STREAM_WATCHLIST if watchlist is None else watchlist
    if not watchlist:
        raise ValueError("empty_stream_watchlist")
    unknown = set(watchlist) - STREAM_PROVIDERS
    if unknown:
        raise ValueError("unknown_stream_source:" + ",".join(sorted(unknown)))
    providers = set(allowed_providers) if allowed_providers is not None else set(watchlist)

    async def _gather() -> list[dict[str, Any]]:
        deadline = clock() + stream_seconds
        connector = connect or _default_connect
        sleeper = sleep or asyncio.sleep
        return await asyncio.gather(*(
            _run_venue(
                provider,
                tuple(instruments),
                store=store,
                allowed_providers=providers,
                max_age_seconds=max_age_seconds,
                deadline=deadline,
                max_messages=max_messages,
                emit_interval=emit_interval_seconds,
                inactivity_seconds=inactivity_seconds,
                max_reconnects=max_reconnects,
                connect=connector,
                clock=clock,
                sleep=sleeper,
                now_fn=now_fn,
            )
            for provider, instruments in sorted(watchlist.items())
        ))

    venue_stats = asyncio.run(_gather())

    totals = {
        key: sum(item[key] for item in venue_stats)
        for key in (
            "connections", "frames", "quotes_parsed", "quotes_emitted",
            "quotes_throttled", "ingested", "duplicates_skipped", "rejected",
            "parse_errors", "reconnects",
        )
    }
    observations = [obs for item in venue_stats for obs in item["emitted_observations"]]
    errors = [
        {"source": item["provider"], "error": str(item["last_error"])}
        for item in venue_stats
        if item["last_error"]
    ]
    collected = {"observations": observations, "errors": errors}
    for item in venue_stats:
        item.pop("emitted_observations")

    return {
        "cycle": "stream_watch",
        "stream_seconds": stream_seconds,
        "venues": {item["provider"]: item for item in venue_stats},
        "totals": totals,
        "coverage": coverage_report(watchlist, collected),
        "push_stream": True,
        "co_location": False,
        "live_execution": False,
        "broker_order_submitted": False,
    }
