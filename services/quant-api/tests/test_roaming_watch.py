"""Tests for the roaming watch: failover, fastest-path, coverage, 24x7 loop.

The "hidden spirit" layer is honest: it fails over only across *officially
documented* alternate domains, cools down and re-probes instead of
hammering, ranks doors by measured round-trip, and never crashes its own
loop. No disguise, no block-evasion — public roads only.
"""
import json
from datetime import UTC, datetime

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.global_market_watch import PROVIDER_BYBIT, PROVIDER_OKX, PROVIDER_STOOQ
from osiris_api.p2p_ingestion import ENABLE_ENV
from osiris_api.roaming_watch import (
    EQUIVALENT_HOSTS,
    MAX_ENDPOINT_ATTEMPTS,
    RoamingTransport,
    coverage_report,
    run_roaming_watch,
)

NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)


class Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def store(tmp_path):
    return ResearchStore(str(tmp_path / "roam.sqlite3"))


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")


# -------------------------------------------------------------- transport

def test_default_alias_map_only_ships_documented_alternates():
    # Bybit publishes api.bytick.com as its alternative global domain.
    assert EQUIVALENT_HOSTS == {"api.bybit.com": ("api.bytick.com",)}


def test_passthrough_success_records_stats():
    clock = Clock()
    roam = RoamingTransport(lambda url, timeout: b"ok", clock=clock)
    assert roam("https://api.bybit.com/v5/x", 5.0) == b"ok"
    entry = roam.stats()["api.bybit.com"]
    assert entry["attempts"] == 1
    assert entry["successes"] == 1
    assert entry["failures"] == 0
    assert entry["ewma_rtt_ms"] >= 0
    assert entry["cooldown_until"] == 0.0


def test_failover_to_documented_alternate():
    clock = Clock()

    def base(url: str, timeout: float) -> bytes:
        if "api.bybit.com" in url:
            raise RuntimeError("watch_source_unavailable")
        return b"from-alt"

    roam = RoamingTransport(base, clock=clock)
    assert roam("https://api.bybit.com/v5/market/tickers?category=spot", 5.0) == b"from-alt"
    stats = roam.stats()
    assert stats["api.bybit.com"]["failures"] == 1
    assert stats["api.bybit.com"]["cooldown_until"] > clock.t
    assert stats["api.bytick.com"]["successes"] == 1


def test_all_doors_failed_raises_last_error_and_cools_both():
    clock = Clock()

    def base(url: str, timeout: float) -> bytes:
        raise RuntimeError("watch_source_unavailable")

    roam = RoamingTransport(base, clock=clock)
    with pytest.raises(RuntimeError, match="watch_source_unavailable"):
        roam("https://api.bybit.com/v5/x", 5.0)
    stats = roam.stats()
    assert stats["api.bybit.com"]["failures"] == 1
    assert stats["api.bytick.com"]["failures"] == 1
    assert stats["api.bytick.com"]["cooldown_until"] > clock.t


def test_value_error_is_surfaced_not_retried():
    clock = Clock()
    calls = []

    def base(url: str, timeout: float) -> bytes:
        calls.append(url)
        raise ValueError("watch_response_too_large")

    roam = RoamingTransport(base, clock=clock)
    with pytest.raises(ValueError, match="watch_response_too_large"):
        roam("https://api.bybit.com/v5/x", 5.0)
    assert calls == ["https://api.bybit.com/v5/x"]  # no door-hopping on content errors


def test_cooled_endpoint_skipped_then_reprobed_after_cooldown():
    clock = Clock()
    calls = []

    def base(url: str, timeout: float) -> bytes:
        calls.append(url)
        if "api.bybit.com" in url and len(calls) == 1:
            raise RuntimeError("watch_source_unavailable")
        if "api.bytick.com" in url and len(calls) == 4:
            raise RuntimeError("watch_source_unavailable")
        return b"ok"

    roam = RoamingTransport(base, clock=clock)
    roam("https://api.bybit.com/v5/x", 5.0)          # primary fails, alt serves
    roam("https://api.bybit.com/v5/x", 5.0)          # primary cooled -> alt directly
    assert "api.bybit.com" in calls[0]
    assert "api.bytick.com" in calls[1] and "api.bytick.com" in calls[2]
    clock.advance(31.0)                              # primary cooldown expires
    roam("https://api.bybit.com/v5/x", 5.0)          # alt fails once -> re-probe primary
    assert "api.bytick.com" in calls[3]              # known-good alt still asked first
    assert "api.bybit.com" in calls[4]               # eligible primary takes over


def test_fastest_path_prefers_lowest_smoothed_rtt():
    clock = Clock()
    calls = []

    def base(url: str, timeout: float) -> bytes:
        calls.append(url)
        if len(calls) == 1:
            clock.advance(0.100)                     # primary: slow 100 ms
            return b"ok"
        if "api.bybit.com" in url and len(calls) == 2:
            raise RuntimeError("watch_source_unavailable")
        clock.advance(0.040)                         # alternate: fast 40 ms
        return b"ok"

    roam = RoamingTransport(base, clock=clock)
    roam("https://api.bybit.com/v5/x", 5.0)          # primary ok, ewma 100 ms
    roam("https://api.bybit.com/v5/x", 5.0)          # primary down -> alt, ewma 40 ms
    clock.advance(31.0)                              # lift primary's cooldown
    roam("https://api.bybit.com/v5/x", 5.0)          # alt now fastest-ranked door
    assert "api.bytick.com" in calls[3]


def test_unmapped_host_has_no_failover():
    clock = Clock()
    calls = []

    def base(url: str, timeout: float) -> bytes:
        calls.append(url)
        raise RuntimeError("watch_source_unavailable")

    roam = RoamingTransport(base, clock=clock)
    with pytest.raises(RuntimeError):
        roam("https://unknown.example.com/x", 5.0)
    assert calls == ["https://unknown.example.com/x"]


def test_invalid_config_rejected():
    with pytest.raises(ValueError, match="invalid_endpoint_host"):
        RoamingTransport(equivalent_hosts={"BAD HOST!": ("ok.example.com",)})
    with pytest.raises(ValueError, match="invalid_endpoint_host"):
        RoamingTransport(equivalent_hosts={"a.example.com": ("a.example.com",)})
    with pytest.raises(ValueError, match="invalid_endpoint_host"):
        RoamingTransport(equivalent_hosts={"a.example.com": ("BAD_HOST",)})
    with pytest.raises(ValueError, match="invalid_cooldown_seconds"):
        RoamingTransport(cooldown_seconds=0.5)
    with pytest.raises(ValueError, match="invalid_cooldown_seconds"):
        RoamingTransport(cooldown_seconds=301.0)


def test_endpoint_attempts_bounded_even_with_many_aliases():
    clock = Clock()
    calls = []

    def base(url: str, timeout: float) -> bytes:
        calls.append(url)
        raise RuntimeError("watch_source_unavailable")

    aliases = tuple(f"alt{i}.example.com" for i in range(6))
    roam = RoamingTransport(
        base, equivalent_hosts={"a.example.com": aliases}, clock=clock
    )
    with pytest.raises(RuntimeError):
        roam("https://a.example.com/x", 5.0)
    assert len(calls) == MAX_ENDPOINT_ATTEMPTS


# --------------------------------------------------------------- coverage

def _collected(observations, errors=()):
    return {"observations": list(observations), "errors": list(errors)}


def _obs(provider, instrument):
    return {"provider": provider, "instrument": instrument}


def test_coverage_report_mixed_cycle():
    watchlist = {"p1": ("A",), "p2": ("A", "B"), "p3": ("C",)}
    collected = _collected(
        [_obs("p1", "A"), _obs("p2", "A"), _obs("p2", "B")],
        errors=[{"source": "p3", "error": "watch_source_unavailable"}],
    )
    report = coverage_report(watchlist, collected)
    assert report["sources_watched"] == 3
    assert report["sources_ok"] == 2
    assert report["failed_sources"] == ["p3"]
    assert report["silent_sources"] == []
    assert report["instrument_sources"] == {"A": ["p1", "p2"], "B": ["p2"]}
    assert report["single_source_instruments"] == ["B"]
    assert report["per_source"]["p3"]["status"] == "error"
    assert report["per_source"]["p1"]["quotes"] == 1
    assert report["live_execution"] is False


def test_coverage_report_flags_silent_source():
    watchlist = {"p1": ("A",), "p2": ("B",)}
    report = coverage_report(watchlist, _collected([_obs("p1", "A")]))
    assert report["silent_sources"] == ["p2"]
    assert report["per_source"]["p2"]["status"] == "silent"
    assert report["sources_ok"] == 1


def test_coverage_report_empty_cycle_everything_blind():
    report = coverage_report({"p1": ("A",), "p2": ("B",)}, _collected([]))
    assert report["sources_ok"] == 0
    assert report["instruments_covered"] == 0
    assert sorted(report["silent_sources"]) == ["p1", "p2"]
    assert report["instrument_sources"] == {}


# ---------------------------------------------------------------- daemon

OKX_TICKERS = json.dumps({
    "code": "0",
    "data": [{"instId": "BTC-USDT", "last": "69001.0", "volCcy24h": "1.0"}],
}).encode()
BYBIT_TICKERS = json.dumps({
    "retCode": 0,
    "result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "69002.5", "volume24h": "1.0"}]},
}).encode()

WATCHLIST = {PROVIDER_OKX: ("BTCUSDT",), PROVIDER_BYBIT: ("BTCUSDT",)}
PROVIDERS = {PROVIDER_OKX, PROVIDER_BYBIT}


def exchange_base(url: str, timeout: float) -> bytes:
    if "okx.com" in url:
        return OKX_TICKERS
    if "bybit.com" in url or "bytick.com" in url:
        return BYBIT_TICKERS
    raise RuntimeError("watch_source_unavailable")


def test_run_roaming_watch_cycles_totals_and_sleep(store, enabled):
    sleeps = []
    result = run_roaming_watch(
        store,
        cycles=3,
        interval_seconds=60.0,
        watchlist=WATCHLIST,
        allowed_providers=PROVIDERS,
        transport=exchange_base,
        sleep=sleeps.append,
    )
    assert result["cycles_run"] == 3
    assert result["stop_reason"] == "cycles_completed"
    assert result["total_fetched"] == 6
    # cycle 1 ingests 2; cycles 2-3 find identical bucketed payloads: duplicates
    assert result["total_ingested"] == 2
    assert result["total_duplicates_skipped"] == 4
    assert sleeps == [60.0, 60.0]
    assert result["endpoint_stats"]
    assert result["co_location"] is False
    assert result["live_execution"] is False
    assert result["broker_order_submitted"] is False


def test_run_roaming_watch_survives_cycle_errors(store, enabled):
    state = {"calls": 0}

    def flaky(url: str, timeout: float) -> bytes:
        state["calls"] += 1
        if state["calls"] <= 2:
            raise TypeError("boom")  # not a per-source error: the cycle blows up
        return exchange_base(url, timeout)

    result = run_roaming_watch(
        store,
        cycles=4,
        interval_seconds=60.0,
        watchlist=WATCHLIST,
        allowed_providers=PROVIDERS,
        transport=flaky,
        sleep=lambda seconds: None,
    )
    assert result["cycles_run"] == 4
    assert result["stop_reason"] == "cycles_completed"
    assert len(result["cycle_errors"]) == 2
    assert "TypeError" in result["cycle_errors"][0]["error"]
    assert result["total_ingested"] == 2  # two good cycles, second dedupes


def test_run_roaming_watch_stops_on_consecutive_errors(store, enabled):
    def broken(url: str, timeout: float) -> bytes:
        raise TypeError("boom")

    result = run_roaming_watch(
        store,
        cycles=10,
        interval_seconds=60.0,
        watchlist=WATCHLIST,
        allowed_providers=PROVIDERS,
        transport=broken,
        max_consecutive_errors=3,
        sleep=lambda seconds: None,
    )
    assert result["cycles_run"] == 3
    assert result["stop_reason"] == "consecutive_errors"
    assert result["consecutive_errors_at_stop"] == 3
    assert result["total_fetched"] == 0


def test_run_roaming_watch_validates_bounds(store):
    with pytest.raises(ValueError, match="invalid_cycles"):
        run_roaming_watch(store, cycles=0)
    with pytest.raises(ValueError, match="invalid_cycles"):
        run_roaming_watch(store, cycles=100_001)
    with pytest.raises(ValueError, match="invalid_interval_seconds"):
        run_roaming_watch(store, cycles=1, interval_seconds=5.0)
    with pytest.raises(ValueError, match="invalid_max_consecutive_errors"):
        run_roaming_watch(store, cycles=1, max_consecutive_errors=0)


def test_run_roaming_watch_kill_switch_off_still_roams(store, monkeypatch):
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    result = run_roaming_watch(
        store,
        cycles=2,
        interval_seconds=60.0,
        watchlist=WATCHLIST,
        allowed_providers=PROVIDERS,
        transport=exchange_base,
        sleep=lambda seconds: None,
    )
    assert result["cycles_run"] == 2
    assert result["total_fetched"] == 4
    assert result["total_ingested"] == 0  # gates still closed; the spirit keeps walking


def test_total_transport_outage_stops_with_source_evidence(store, enabled):
    clock = Clock()
    sleeps = []

    def unavailable(url, timeout):
        raise RuntimeError("watch_source_unavailable")

    def sleep(seconds):
        sleeps.append(seconds)
        clock.advance(seconds)

    result = run_roaming_watch(
        store, cycles=10, watchlist=WATCHLIST, transport=unavailable,
        max_consecutive_errors=2, clock=clock, sleep=sleep,
    )
    assert result["cycles_run"] == 2
    assert result["stop_reason"] == "consecutive_errors"
    assert result["consecutive_errors_at_stop"] == 2
    assert result["total_fetched"] == 0
    assert sleeps == [60.0]  # no wait after stopping
    assert result["cycle_errors"] == [
        {"cycle": "0", "error": "watch_no_observations"},
        {"cycle": "1", "error": "watch_no_observations"},
    ]
    assert result["latest_coverage"]["missing_instruments"] == ["BTCUSDT"]
    assert result["latest_coverage"]["failed_sources"] == sorted(PROVIDERS)
    assert all(report["status"] == "no_observations" for report in result["cycle_reports"])
    assert all(len(report["source_errors"]) == 2 for report in result["cycle_reports"])


def test_silent_sources_count_toward_outage_threshold(store, enabled):
    result = run_roaming_watch(
        store, cycles=5, watchlist={PROVIDER_OKX: ("BTCUSDT",)},
        transport=lambda url, timeout: b'{"code":"0","data":[]}',
        max_consecutive_errors=1, sleep=lambda seconds: None,
    )
    assert result["stop_reason"] == "consecutive_errors"
    assert result["cycles_run"] == 1
    assert result["latest_coverage"]["silent_sources"] == [PROVIDER_OKX]
    assert result["cycle_reports"][0]["source_errors"] == []


def test_useful_partial_coverage_resets_outage_streak(store, enabled):
    clock = Clock()
    state = {"cycle": 0}

    def transport(url, timeout):
        if state["cycle"] == 1 and "okx.com" in url:
            return OKX_TICKERS
        raise RuntimeError("watch_source_unavailable")

    def sleep(seconds):
        state["cycle"] += 1
        clock.advance(seconds)

    result = run_roaming_watch(
        store, cycles=3, watchlist=WATCHLIST, transport=transport,
        max_consecutive_errors=2, clock=clock, sleep=sleep,
    )
    assert result["stop_reason"] == "cycles_completed"
    assert result["consecutive_errors_at_stop"] == 1
    assert result["total_fetched"] == 1
    assert result["total_ingested"] == 1
    reports = result["cycle_reports"]
    assert [report["status"] for report in reports] == [
        "no_observations", "degraded", "no_observations",
    ]
    assert reports[1]["coverage"]["single_source_instruments"] == ["BTCUSDT"]
    assert reports[1]["coverage"]["failed_sources"] == [PROVIDER_BYBIT]


def test_cycle_evidence_retains_only_latest_fifty_reports(store):
    result = run_roaming_watch(
        store, cycles=55, watchlist={PROVIDER_OKX: ("BTCUSDT",)},
        transport=lambda url, timeout: b'{"code":"0","data":[]}',
        max_consecutive_errors=100, sleep=lambda seconds: None,
    )
    assert result["cycles_run"] == 55
    assert result["consecutive_errors_at_stop"] == 55
    assert len(result["cycle_errors"]) == len(result["cycle_reports"]) == 50
    assert result["cycle_errors"][0]["cycle"] == "5"
    assert result["cycle_reports"][0]["cycle"] == 5
    assert result["cycle_reports"][-1]["cycle"] == 54


def test_explicit_empty_watchlist_is_idle_without_network_requests(store):
    def unexpected_request(url, timeout):
        pytest.fail("empty watchlist must not fetch default sources")

    result = run_roaming_watch(
        store, cycles=2, watchlist={}, transport=unexpected_request,
        max_consecutive_errors=1, sleep=lambda seconds: None,
    )
    assert result["stop_reason"] == "cycles_completed"
    assert result["consecutive_errors_at_stop"] == 0
    assert result["total_fetched"] == 0
    assert result["latest_coverage"]["sources_watched"] == 0
    assert [report["status"] for report in result["cycle_reports"]] == ["idle", "idle"]


def test_unrequested_quotes_cannot_reset_total_coverage_outage(store):
    # A source may ignore its requested symbols and return unrelated quotes.
    body = (
        b"Symbol,Date,Time,Open,High,Low,Close,Volume\n"
        b"^NDQ,2026-11-06,12:00:00,100,110,90,105,0\n"
    )
    result = run_roaming_watch(
        store, cycles=2, watchlist={PROVIDER_STOOQ: ("^spx",)},
        transport=lambda url, timeout: body,
        max_consecutive_errors=1, sleep=lambda seconds: None,
    )
    assert result["stop_reason"] == "consecutive_errors"
    assert result["cycles_run"] == 1
    assert result["cycle_reports"][0]["status"] == "no_observations"
    assert result["latest_coverage"]["instruments_covered"] == 0
    assert result["latest_coverage"]["missing_instruments"] == ["^SPX"]
