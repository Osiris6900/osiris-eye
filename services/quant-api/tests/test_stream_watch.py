"""Tests for the stream watch: parsers, session bounds, throttling, gates.

No network: scripted fake sockets and a fake clock drive every session.
The spirit listens on public feeds only — confirmations and heartbeats are
not quotes, malformed market data raises, and a quiet market still dedupes.
Sessions end on their frame budget, so no test ever touches a real socket.
"""
import json
from datetime import datetime, timezone

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.global_market_watch import (
    PROVIDER_BINANCE,
    PROVIDER_BYBIT,
    PROVIDER_COINBASE,
    PROVIDER_OKX,
)
from osiris_api.p2p_ingestion import DATASET, ENABLE_ENV
from osiris_api.stream_watch import (
    DEFAULT_STREAM_WATCHLIST,
    STREAM_PROVIDERS,
    _stream_url,
    _subscribe_frames,
    _venue_product,
    parse_stream_message,
    run_stream_watch,
)

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)
VENUE_TS_MS = int(NOW.timestamp() * 1000) - 250  # venue tick 250 ms before receipt


class Clock:
    def __init__(self, start: float = 5000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeSocket:
    """Scripted stand-in for a websocket connection."""

    def __init__(self, frames=(), *, clock=None, step=0.0, enter_error=None, recv_error=None):
        self._frames = list(frames)
        self._clock = clock
        self._step = step
        self._enter_error = enter_error
        self._recv_error = recv_error
        self.sent: list[str] = []

    async def __aenter__(self):
        if self._enter_error is not None:
            raise self._enter_error
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, frame):
        self.sent.append(frame)

    async def recv(self):
        if self._recv_error is not None:
            raise self._recv_error
        if self._frames:
            if self._clock is not None and self._step:
                self._clock.advance(self._step)
            return self._frames.pop(0)
        raise ConnectionError("stream_closed")  # safety: tests end on frame budget


class FakeConnect:
    def __init__(self, *sockets):
        self._sockets = list(sockets)
        self.urls: list[str] = []

    def __call__(self, url):
        self.urls.append(url)
        if not self._sockets:
            raise ConnectionError("no_more_sockets")
        return self._sockets.pop(0)


async def _no_sleep(seconds):
    return None


@pytest.fixture
def store(tmp_path):
    return ResearchStore(str(tmp_path / "stream.sqlite3"))


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")


def binance_tick(price="69001.0", symbol="BTCUSDT", ts=VENUE_TS_MS):
    return json.dumps({
        "stream": symbol.lower() + "@miniTicker",
        "data": {"e": "24hrMiniTicker", "E": ts, "s": symbol, "c": price, "v": "12.5"},
    })


def okx_tick(price="69001.0", inst="BTC-USDT", ts=str(VENUE_TS_MS)):
    return json.dumps({
        "arg": {"channel": "tickers", "instId": inst},
        "data": [{"instId": inst, "last": price, "volCcy24h": "9.5", "ts": ts}],
    })


def bybit_tick(price="69002.5", symbol="BTCUSDT", ts=VENUE_TS_MS):
    return json.dumps({
        "topic": f"tickers.{symbol}", "type": "snapshot", "ts": ts,
        "data": {"symbol": symbol, "lastPrice": price, "volume24h": "7.5"},
    })


def coinbase_tick(price="69003.0", product="BTC-USD"):
    return json.dumps({
        "type": "ticker", "product_id": product, "price": price,
        "volume_24h": "5.5", "time": "2027-05-23T09:35:14.123456Z",
    })


def _run(store, clock, sockets, **kwargs):
    kwargs.setdefault("stream_seconds", 60.0)
    kwargs.setdefault("inactivity_seconds", 5.0)
    kwargs.setdefault("connect", FakeConnect(*sockets))
    kwargs.setdefault("clock", clock)
    kwargs.setdefault("now_fn", lambda: NOW)
    kwargs.setdefault("sleep", _no_sleep)
    return run_stream_watch(store, **kwargs)


# -------------------------------------------------------------- wiring

def test_default_watchlist_covers_exactly_the_stream_providers():
    assert set(DEFAULT_STREAM_WATCHLIST) == set(STREAM_PROVIDERS)
    assert STREAM_PROVIDERS == {
        PROVIDER_BINANCE, PROVIDER_OKX, PROVIDER_BYBIT, PROVIDER_COINBASE,
    }


def test_binance_url_subscribes_via_query_and_sends_no_frames():
    url = _stream_url(PROVIDER_BINANCE, ("BTCUSDT", "ETHUSDT"))
    assert url == (
        "wss://stream.binance.com:9443/stream"
        "?streams=btcusdt@miniTicker/ethusdt@miniTicker"
    )
    assert _subscribe_frames(PROVIDER_BINANCE, ("BTCUSDT",)) == []


def test_subscribe_frames_match_each_venue_protocol():
    okx = json.loads(_subscribe_frames(PROVIDER_OKX, ("BTCUSDT",))[0])
    assert okx == {"op": "subscribe", "args": [{"channel": "tickers", "instId": "BTC-USDT"}]}
    bybit = json.loads(_subscribe_frames(PROVIDER_BYBIT, ("BTCUSDT",))[0])
    assert bybit == {"op": "subscribe", "args": ["tickers.BTCUSDT"]}
    coinbase = json.loads(_subscribe_frames(PROVIDER_COINBASE, ("BTC",))[0])
    assert coinbase == {"type": "subscribe", "product_ids": ["BTC-USD"], "channels": ["ticker"]}


def test_venue_product_mapping_and_rejections():
    assert _venue_product(PROVIDER_BINANCE, "BTCUSDT") == "btcusdt"
    assert _venue_product(PROVIDER_OKX, "ETHUSDT") == "ETH-USDT"
    assert _venue_product(PROVIDER_BYBIT, "ETHUSDT") == "ETHUSDT"
    assert _venue_product(PROVIDER_COINBASE, "BTC") == "BTC-USD"
    with pytest.raises(ValueError, match="unknown_stream_instrument"):
        _venue_product(PROVIDER_BINANCE, "BTC")  # USD pair has no USDT base
    with pytest.raises(ValueError, match="unknown_stream_source"):
        _venue_product("kraken-public", "BTC")


# -------------------------------------------------------------- parsers

def test_parse_binance_miniticker():
    quotes = parse_stream_message(PROVIDER_BINANCE, binance_tick("69001.0"))
    assert len(quotes) == 1
    quote = quotes[0]
    assert quote["instrument"] == "BTCUSDT"
    assert quote["price"] == 69001.0
    assert quote["volume_24h"] == 12.5
    assert quote["venue_event_at"] == datetime.fromtimestamp(VENUE_TS_MS / 1000, tz=timezone.utc)


def test_parse_control_frames_are_not_quotes():
    assert parse_stream_message(PROVIDER_BINANCE, '{"stream":"x","data":{"e":"aggTrade"}}') == []
    assert parse_stream_message(PROVIDER_OKX, "pong") == []
    assert parse_stream_message(PROVIDER_OKX, '{"event":"subscribe","arg":{"channel":"tickers"}}') == []
    assert parse_stream_message(PROVIDER_BYBIT, '{"success":true,"op":"subscribe"}') == []
    assert parse_stream_message(PROVIDER_COINBASE, '{"type":"subscriptions","channels":[]}') == []
    assert parse_stream_message(PROVIDER_COINBASE, "") == []


def test_parse_okx_tickers_batch():
    quotes = parse_stream_message(PROVIDER_OKX, okx_tick("69001.0"))
    assert quotes[0]["instrument"] == "BTCUSDT"
    assert quotes[0]["price"] == 69001.0
    assert quotes[0]["venue_event_at"] is not None


def test_parse_bybit_snapshot():
    quotes = parse_stream_message(PROVIDER_BYBIT, bybit_tick("69002.5"))
    assert quotes[0]["instrument"] == "BTCUSDT"
    assert quotes[0]["price"] == 69002.5


def test_parse_coinbase_ticker_maps_product_to_canonical():
    quotes = parse_stream_message(PROVIDER_COINBASE, coinbase_tick("69003.0"))
    assert quotes[0]["instrument"] == "BTC"
    assert quotes[0]["price"] == 69003.0
    assert quotes[0]["venue_event_at"] == datetime(2027, 5, 23, 9, 35, 14, 123456,
                                                   tzinfo=timezone.utc)


def test_parse_bad_price_raises_never_guesses():
    with pytest.raises(ValueError, match="invalid_quote_value"):
        parse_stream_message(PROVIDER_BINANCE, binance_tick(price="not-a-number"))
    with pytest.raises(ValueError, match="invalid_quote_value"):
        parse_stream_message(PROVIDER_COINBASE, coinbase_tick(price="-1"))


def test_parse_invalid_frame_and_unknown_source():
    with pytest.raises(ValueError, match="invalid_stream_frame"):
        parse_stream_message(PROVIDER_BINANCE, "{not json")
    with pytest.raises(ValueError, match="unknown_stream_source"):
        parse_stream_message("kraken-public", "{}")


# -------------------------------------------------------------- sessions

def test_session_ingests_and_throttles(store, enabled):
    clock = Clock()
    socket = FakeSocket(
        [okx_tick("69001.0"), okx_tick("69002.0"), okx_tick("69003.0")],
        clock=clock, step=0.4,  # 0.4 s apart: inside the 1 s emit interval
    )
    result = _run(store, clock, [socket],
                  watchlist={PROVIDER_OKX: ("BTCUSDT",)}, max_messages=3)
    venue = result["venues"][PROVIDER_OKX]
    assert venue["connections"] == 1
    assert venue["frames"] == 3
    assert venue["quotes_parsed"] == 3
    assert venue["quotes_emitted"] == 1       # 0.4 s apart → ticks 2-3 throttled
    assert venue["quotes_throttled"] == 2
    assert venue["ingested"] == 1
    assert venue["stop_reason"] == "frame_budget_spent"
    assert json.loads(socket.sent[0])["op"] == "subscribe"
    assert result["totals"]["ingested"] == 1
    assert result["push_stream"] is True
    assert result["co_location"] is False
    assert result["live_execution"] is False


def test_session_duplicate_price_dedupes(store, enabled):
    clock = Clock()
    socket = FakeSocket(
        [okx_tick("69001.0"), okx_tick("69001.0")],
        clock=clock, step=2.0,  # past the emit interval, same price
    )
    result = _run(store, clock, [socket],
                  watchlist={PROVIDER_OKX: ("BTCUSDT",)}, max_messages=2)
    venue = result["venues"][PROVIDER_OKX]
    assert venue["quotes_emitted"] == 2
    assert venue["ingested"] == 1
    assert venue["duplicates_skipped"] == 1


def test_stream_payload_carries_push_provenance(store, enabled):
    clock = Clock()
    socket = FakeSocket([okx_tick("69001.0")], clock=clock, step=0.5)
    _run(store, clock, [socket], watchlist={PROVIDER_OKX: ("BTCUSDT",)}, max_messages=1)
    rows = store.list(DATASET, limit=5)
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"]["payload"])  # ingestion wraps schema+provider
    assert payload["feed"] == "websocket_push"
    assert payload["venue_ts_source"] == "venue"
    assert payload["venue_event_age_ms"] == 250.0  # received minus venue ts, 5 ms bucket
    assert payload["clock_skew_note"] == "age_includes_node_clock_skew"
    assert payload["price"] == 69001.0


def test_missing_venue_timestamp_marks_node_source(store, enabled):
    clock = Clock()
    tick = json.dumps({
        "arg": {"channel": "tickers", "instId": "BTC-USDT"},
        "data": [{"instId": "BTC-USDT", "last": "69001.0"}],  # no ts field
    })
    socket = FakeSocket([tick], clock=clock, step=0.5)
    _run(store, clock, [socket], watchlist={PROVIDER_OKX: ("BTCUSDT",)}, max_messages=1)
    payload = json.loads(store.list(DATASET, limit=5)[0]["payload"]["payload"])
    assert payload["venue_ts_source"] == "node"
    assert "venue_event_age_ms" not in payload


def test_parse_error_counted_session_continues(store, enabled):
    clock = Clock()
    socket = FakeSocket(
        ["{broken", okx_tick("69001.0"), "{still broken"],
        clock=clock, step=0.5,
    )
    result = _run(store, clock, [socket],
                  watchlist={PROVIDER_OKX: ("BTCUSDT",)}, max_messages=3)
    venue = result["venues"][PROVIDER_OKX]
    assert venue["parse_errors"] == 2
    assert venue["ingested"] == 1


def test_unrequested_instrument_never_adopted(store, enabled):
    clock = Clock()
    socket = FakeSocket(
        [okx_tick("111.0", inst="ETH-USDT"), okx_tick("69001.0")],
        clock=clock, step=0.5,
    )
    result = _run(store, clock, [socket],
                  watchlist={PROVIDER_OKX: ("BTCUSDT",)}, max_messages=2)
    venue = result["venues"][PROVIDER_OKX]
    assert venue["quotes_parsed"] == 2
    assert venue["quotes_emitted"] == 1  # ETH-USDT was never requested
    rows = store.list(DATASET, limit=10)
    assert [row["record_key"].split(":")[0] for row in rows] == ["BTCUSDT"]


def test_kill_switch_off_gates_every_streamed_quote(store, monkeypatch):
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    clock = Clock()
    socket = FakeSocket([okx_tick("69001.0"), okx_tick("69002.0")], clock=clock, step=2.0)
    result = _run(store, clock, [socket],
                  watchlist={PROVIDER_OKX: ("BTCUSDT",)}, max_messages=2)
    venue = result["venues"][PROVIDER_OKX]
    assert venue["quotes_emitted"] == 2
    assert venue["ingested"] == 0
    assert venue["rejected"] == 2  # gates closed: surfaced, never stored


def test_reconnect_after_drop_recovers(store, enabled):
    clock = Clock()
    dead = FakeSocket(recv_error=ConnectionError("dropped"))
    alive = FakeSocket([okx_tick("69001.0")], clock=clock, step=0.5)
    result = _run(store, clock, [dead, alive],
                  watchlist={PROVIDER_OKX: ("BTCUSDT",)}, max_messages=1, max_reconnects=2)
    venue = result["venues"][PROVIDER_OKX]
    assert venue["connections"] == 2
    assert venue["reconnects"] == 1
    assert venue["ingested"] == 1
    assert venue["stop_reason"] == "frame_budget_spent"


def test_reconnects_exhausted_stops_honestly(store, enabled):
    clock = Clock()
    sockets = [FakeSocket(enter_error=ConnectionError("refused")) for _ in range(3)]
    result = _run(store, clock, sockets,
                  watchlist={PROVIDER_OKX: ("BTCUSDT",)}, max_reconnects=2)
    venue = result["venues"][PROVIDER_OKX]
    assert venue["connections"] == 0  # never even entered
    assert venue["reconnects"] == 2
    assert venue["stop_reason"] == "reconnects_exhausted"
    assert "refused" in venue["last_error"]
    assert result["coverage"]["failed_sources"] == [PROVIDER_OKX]


def test_two_venues_share_one_instrument(store, enabled):
    clock = Clock()
    okx_socket = FakeSocket([okx_tick("69001.0")], clock=clock, step=0.5)
    bybit_socket = FakeSocket([bybit_tick("69002.5")], clock=clock, step=0.5)
    connects = {PROVIDER_OKX: FakeConnect(okx_socket), PROVIDER_BYBIT: FakeConnect(bybit_socket)}

    def router(url):
        if "okx" in url:
            return connects[PROVIDER_OKX](url)
        return connects[PROVIDER_BYBIT](url)

    result = _run(store, clock, [],
                  watchlist={PROVIDER_OKX: ("BTCUSDT",), PROVIDER_BYBIT: ("BTCUSDT",)},
                  connect=router, max_messages=1)
    assert result["totals"]["ingested"] == 2
    sources = result["coverage"]["instrument_sources"]["BTCUSDT"]
    assert sources == [PROVIDER_BYBIT, PROVIDER_OKX]
    assert result["coverage"]["single_source_instruments"] == []


def test_run_validates_bounds(store):
    with pytest.raises(ValueError, match="invalid_stream_seconds"):
        run_stream_watch(store, stream_seconds=4.0)
    with pytest.raises(ValueError, match="invalid_stream_seconds"):
        run_stream_watch(store, stream_seconds=90_000.0)
    with pytest.raises(ValueError, match="invalid_max_messages"):
        run_stream_watch(store, stream_seconds=60.0, max_messages=0)
    with pytest.raises(ValueError, match="invalid_emit_interval_seconds"):
        run_stream_watch(store, stream_seconds=60.0, emit_interval_seconds=0.1)
    with pytest.raises(ValueError, match="invalid_inactivity_seconds"):
        run_stream_watch(store, stream_seconds=60.0, inactivity_seconds=4.0)
    with pytest.raises(ValueError, match="invalid_max_reconnects"):
        run_stream_watch(store, stream_seconds=60.0, max_reconnects=11)
    with pytest.raises(ValueError, match="unknown_stream_source"):
        run_stream_watch(store, stream_seconds=60.0, watchlist={"kraken-public": ("BTC",)})
    with pytest.raises(ValueError, match="empty_stream_watchlist"):
        run_stream_watch(store, stream_seconds=60.0, watchlist={})
