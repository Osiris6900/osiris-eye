"""Tests for the stream fleet: many spirits per venue, looping 24x7.

No network: scripted fake sockets and a fake clock drive every session.
The fleet is honest about what it is — spirits are concurrent public
connections through each venue's own doorway, capped as polite guests;
redundancy shows up as measured duplicates; a dead venue never takes the
fleet down; a cycle that explodes is recorded and survived.
"""

import json
from datetime import datetime, timezone

import pytest

from osiris_api import stream_fleet
from osiris_api.data_store import ResearchStore
from osiris_api.global_market_watch import PROVIDER_BINANCE, PROVIDER_OKX
from osiris_api.p2p_ingestion import DATASET, ENABLE_ENV
from osiris_api.stream_fleet import (
    DEFAULT_SPIRITS_PER_VENUE,
    MAX_FLEET_CYCLES,
    MAX_SPIRITS_PER_VENUE,
    MIN_SPIRITS_PER_VENUE,
    run_stream_fleet,
)
from osiris_api.stream_watch import DEFAULT_STREAM_WATCHLIST

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)
VENUE_TS_MS = int(NOW.timestamp() * 1000) - 250


class Clock:
    def __init__(self, start: float = 5000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeSocket:
    """Scripted stand-in for a websocket connection."""

    def __init__(self, frames=(), *, clock=None, step=0.0):
        self._frames = list(frames)
        self._clock = clock
        self._step = step
        self.sent: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, frame):
        self.sent.append(frame)

    async def recv(self):
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


class RoutedConnect:
    """Per-URL connect: binance always fails, every other URL gets a socket."""

    def __init__(self, *sockets):
        self._sockets = list(sockets)

    def __call__(self, url):
        if "binance" in url:
            raise ConnectionError("binance_unreachable")
        if not self._sockets:
            raise ConnectionError("no_more_sockets")
        return self._sockets.pop(0)


async def _no_sleep(seconds):
    return None


@pytest.fixture
def store(tmp_path):
    return ResearchStore(str(tmp_path / "fleet.sqlite3"))


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")


def binance_tick(price="69001.0", symbol="BTCUSDT", ts=VENUE_TS_MS):
    return json.dumps(
        {
            "stream": symbol.lower() + "@miniTicker",
            "data": {"e": "24hrMiniTicker", "E": ts, "s": symbol, "c": price, "v": "12.5"},
        }
    )


def okx_tick(price="69001.0", inst="BTC-USDT", ts=str(VENUE_TS_MS)):
    return json.dumps(
        {
            "arg": {"channel": "tickers", "instId": inst},
            "data": [{"instId": inst, "last": price, "volCcy24h": "9.5", "ts": ts}],
        }
    )


def _run(store, clock, connect, **kwargs):
    kwargs.setdefault("stream_seconds", 60.0)
    kwargs.setdefault("inactivity_seconds", 5.0)
    kwargs.setdefault("connect", connect)
    kwargs.setdefault("clock", clock)
    kwargs.setdefault("now_fn", lambda: NOW)
    kwargs.setdefault("sleep", _no_sleep)
    kwargs.setdefault("spirit_stagger_seconds", 0.0)
    return run_stream_fleet(store, **kwargs)


# -------------------------------------------------------------- the fleet


def test_fleet_runs_many_spirits_per_venue(store, enabled):
    clock = Clock()
    connect = FakeConnect(
        FakeSocket([binance_tick()]),
        FakeSocket([binance_tick()]),
        FakeSocket([okx_tick()]),
        FakeSocket([okx_tick()]),
    )
    result = _run(
        store,
        clock,
        connect,
        watchlist={PROVIDER_BINANCE: ("BTCUSDT",), PROVIDER_OKX: ("BTCUSDT",)},
        spirits_per_venue=2,
        max_messages=1,
    )
    assert result["cycle"] == "stream_fleet"
    assert result["cycles_run"] == 1
    assert result["stop_reason"] == "cycles_completed"
    assert result["spirits_per_venue"] == 2
    assert result["spirits_total"] == 4
    assert set(result["last_session_spirits"]) == {
        f"{PROVIDER_BINANCE}#spirit-0",
        f"{PROVIDER_BINANCE}#spirit-1",
        f"{PROVIDER_OKX}#spirit-0",
        f"{PROVIDER_OKX}#spirit-1",
    }
    assert result["totals"]["connections"] == 4
    assert result["totals"]["frames"] == 4
    assert len(connect.urls) == 4


def test_spirits_share_one_store_and_identical_ticks_dedupe(store, enabled):
    """Redundancy is measured: the second spirit's identical tick is a
    counted duplicate — proof the spirits agree, not a silent assumption."""
    clock = Clock()
    connect = FakeConnect(FakeSocket([binance_tick()]), FakeSocket([binance_tick()]))
    result = _run(
        store,
        clock,
        connect,
        watchlist={PROVIDER_BINANCE: ("BTCUSDT",)},
        spirits_per_venue=2,
        max_messages=1,
    )
    assert result["totals"]["quotes_emitted"] == 2
    assert result["totals"]["ingested"] == 1
    assert result["totals"]["duplicates_skipped"] == 1
    rows = store.list(DATASET, limit=5)
    assert len(rows) == 1
    assert rows[0]["record_key"].split(":")[0] == "BTCUSDT"
    assert json.loads(rows[0]["payload"]["payload"])["feed"] == "websocket_push"


def test_fleet_cycles_respawn_sessions_and_totals_accumulate(store, enabled):
    clock = Clock()
    connect = FakeConnect(*(FakeSocket([okx_tick()]) for _ in range(3)))
    result = _run(
        store,
        clock,
        connect,
        watchlist={PROVIDER_OKX: ("BTCUSDT",)},
        spirits_per_venue=1,
        cycles=3,
        max_messages=1,
    )
    assert result["cycles_run"] == 3
    assert result["cycles_requested"] == 3
    assert result["stop_reason"] == "cycles_completed"
    assert result["totals"]["connections"] == 3  # one respawn per cycle
    assert result["totals"]["ingested"] == 1
    assert result["totals"]["duplicates_skipped"] == 2  # same tick, every cycle


def test_cycle_explosion_is_contained_and_error_budget_stops(store, enabled, monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(stream_fleet, "_run_venue", _boom)
    clock = Clock()
    result = _run(
        store,
        clock,
        FakeConnect(),
        watchlist={PROVIDER_OKX: ("BTCUSDT",)},
        cycles=5,
        max_consecutive_errors=2,
        max_messages=1,
    )
    assert result["stop_reason"] == "consecutive_errors"
    assert result["consecutive_errors_at_stop"] == 2
    assert result["cycles_run"] == 2  # gave up honestly, not at cycle 5
    assert [entry["error"] for entry in result["cycle_errors"]] == [
        "RuntimeError:boom",
        "RuntimeError:boom",
    ]


def test_dead_venue_is_contained_and_marked_failed_in_coverage(store, enabled):
    clock = Clock()
    connect = RoutedConnect(FakeSocket([okx_tick()]))
    result = _run(
        store,
        clock,
        connect,
        watchlist={PROVIDER_BINANCE: ("BTCUSDT",), PROVIDER_OKX: ("BTCUSDT",)},
        spirits_per_venue=1,
        max_reconnects=1,
        max_messages=1,
    )
    assert result["stop_reason"] == "cycles_completed"  # the fleet walked on
    assert result["totals"]["ingested"] == 1  # okx carried the session
    coverage = result["coverage"]
    assert coverage["failed_sources"] == [PROVIDER_BINANCE]
    assert coverage["per_source"][PROVIDER_BINANCE]["status"] == "error"
    assert coverage["per_source"][PROVIDER_OKX]["status"] == "ok"
    assert coverage["instruments_covered"] == 1
    spirit = result["last_session_spirits"][f"{PROVIDER_BINANCE}#spirit-0"]
    assert spirit["stop_reason"] == "reconnects_exhausted"
    assert "binance_unreachable" in spirit["last_error"]


def test_spirits_are_staggered(store, enabled):
    sleeps: list[float] = []

    async def _recording_sleep(seconds):
        sleeps.append(seconds)

    clock = Clock()
    connect = FakeConnect(FakeSocket([okx_tick()]), FakeSocket([okx_tick()]))
    _run(
        store,
        clock,
        connect,
        watchlist={PROVIDER_OKX: ("BTCUSDT",)},
        spirits_per_venue=2,
        max_messages=1,
        spirit_stagger_seconds=0.5,
        sleep=_recording_sleep,
    )
    assert sleeps == [0.5]  # spirit-0 never waits; spirit-1 waits one stagger


def test_single_spirit_per_venue_is_the_floor(store, enabled):
    clock = Clock()
    connect = FakeConnect(FakeSocket([okx_tick()]))
    result = _run(
        store,
        clock,
        connect,
        watchlist={PROVIDER_OKX: ("BTCUSDT",)},
        spirits_per_venue=1,
        max_messages=1,
    )
    assert result["spirits_total"] == 1
    assert result["totals"]["ingested"] == 1


def test_kill_switch_off_gates_every_spirit(store, monkeypatch):
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    clock = Clock()
    connect = FakeConnect(FakeSocket([okx_tick()]), FakeSocket([okx_tick()]))
    result = _run(
        store,
        clock,
        connect,
        watchlist={PROVIDER_OKX: ("BTCUSDT",)},
        spirits_per_venue=2,
        max_messages=1,
    )
    assert result["totals"]["quotes_emitted"] == 2
    assert result["totals"]["ingested"] == 0
    assert result["totals"]["rejected"] == 2  # gates closed: surfaced, never stored
    assert store.list(DATASET, limit=5) == []


def test_result_shape_and_honest_flags(store, enabled):
    clock = Clock()
    result = _run(
        store,
        clock,
        FakeConnect(FakeSocket([okx_tick()])),
        watchlist={PROVIDER_OKX: ("BTCUSDT",)},
        spirits_per_venue=1,
        max_messages=1,
    )
    assert result["push_stream"] is True
    assert result["co_location"] is False  # never claimed
    assert result["live_execution"] is False
    assert result["broker_order_submitted"] is False
    assert result["elapsed_seconds"] >= 0.0
    assert result["coverage"]["live_execution"] is False


def test_guest_cap_and_cycle_bounds_are_honest():
    assert MIN_SPIRITS_PER_VENUE == 1
    assert MAX_SPIRITS_PER_VENUE == 4  # polite guest, far inside venue limits
    assert DEFAULT_SPIRITS_PER_VENUE == 2
    assert MAX_FLEET_CYCLES == 100_000
    assert set(DEFAULT_STREAM_WATCHLIST)  # the fleet defaults to the same venues


# -------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"spirits_per_venue": 0}, "invalid_spirits_per_venue"),
        ({"spirits_per_venue": 5}, "invalid_spirits_per_venue"),
        ({"spirits_per_venue": 2.5}, "invalid_spirits_per_venue"),
        ({"stream_seconds": 1.0}, "invalid_stream_seconds"),
        ({"cycles": 0}, "invalid_cycles"),
        ({"cycles": 100_001}, "invalid_cycles"),
        ({"cycles": 1.5}, "invalid_cycles"),
        ({"max_messages": 0}, "invalid_max_messages"),
        ({"emit_interval_seconds": 0.01}, "invalid_emit_interval_seconds"),
        ({"inactivity_seconds": 1.0}, "invalid_inactivity_seconds"),
        ({"max_reconnects": -1}, "invalid_max_reconnects"),
        ({"max_reconnects": 11}, "invalid_max_reconnects"),
        ({"max_consecutive_errors": 0}, "invalid_max_consecutive_errors"),
        ({"spirit_stagger_seconds": -0.1}, "invalid_spirit_stagger_seconds"),
        ({"spirit_stagger_seconds": 6.0}, "invalid_spirit_stagger_seconds"),
        ({"watchlist": {}}, "empty_stream_watchlist"),
        ({"watchlist": {"kraken-public": ("BTC",)}}, "unknown_stream_source"),
    ],
)
def test_invalid_arguments_raise(store, override, match):
    kwargs = {
        "stream_seconds": 60.0,
        "watchlist": {PROVIDER_OKX: ("BTCUSDT",)},
        "connect": FakeConnect(),
    }
    kwargs.update(override)
    with pytest.raises(ValueError, match=match):
        run_stream_fleet(store, **kwargs)
