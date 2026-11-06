"""Cooldown regression tests with no network access or wall-clock sleeps."""
from urllib.parse import urlsplit

import pytest

from osiris_api.roaming_watch import MAX_ENDPOINT_ATTEMPTS, RoamingTransport


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_total_outage_does_not_retry_any_host_during_cooldown():
    clock = Clock()
    calls = []

    def unavailable(url, timeout):
        calls.append(url)
        raise RuntimeError("watch_source_unavailable")

    roam = RoamingTransport(unavailable, clock=clock, cooldown_seconds=30.0)
    with pytest.raises(RuntimeError, match="watch_source_unavailable"):
        roam("https://api.bybit.com/v5/x", 5.0)
    before = roam.stats()
    clock.advance(29.0)
    with pytest.raises(RuntimeError, match="watch_endpoints_cooling_down"):
        roam("https://api.bybit.com/v5/x", 5.0)
    assert len(calls) == 2
    assert roam.stats() == before

    clock.advance(1.0)
    with pytest.raises(RuntimeError, match="watch_source_unavailable"):
        roam("https://api.bybit.com/v5/x", 5.0)
    assert len(calls) == 4


def test_unmapped_endpoint_obeys_cooldown_too():
    clock = Clock()
    calls = []

    def unavailable(url, timeout):
        calls.append(url)
        raise RuntimeError("watch_source_unavailable")

    roam = RoamingTransport(unavailable, clock=clock)
    with pytest.raises(RuntimeError, match="watch_source_unavailable"):
        roam("https://one.example.com/x", 5.0)
    with pytest.raises(RuntimeError, match="watch_endpoints_cooling_down"):
        roam("https://one.example.com/x", 5.0)
    assert len(calls) == 1


def test_live_alternate_serves_while_primary_cools():
    clock = Clock()
    calls = []

    def fallback(url, timeout):
        host = urlsplit(url).hostname
        calls.append(host)
        if host == "api.bybit.com":
            raise RuntimeError("watch_source_unavailable")
        return b"quote"

    roam = RoamingTransport(fallback, clock=clock)
    assert roam("https://api.bybit.com/v5/x", 5.0) == b"quote"
    assert roam("https://api.bybit.com/v5/x", 5.0) == b"quote"
    assert calls == ["api.bybit.com", "api.bytick.com", "api.bytick.com"]


def test_success_after_expiry_clears_old_failure_state():
    clock = Clock()
    healthy = False

    def recovering(url, timeout):
        if not healthy:
            raise RuntimeError("watch_source_unavailable")
        clock.advance(0.025)
        return b"recovered"

    roam = RoamingTransport(recovering, clock=clock)
    with pytest.raises(RuntimeError):
        roam("https://one.example.com/x", 5.0)
    healthy = True
    clock.advance(30.0)
    assert roam("https://one.example.com/x", 5.0) == b"recovered"
    entry = roam.stats()["one.example.com"]
    assert entry["cooldown_until"] == 0.0
    assert entry["last_error"] is None
    assert entry["attempts"] == 2
    assert entry["successes"] == 1
    assert entry["failures"] == 1
    assert entry["last_rtt_ms"] == 25.0


def test_attempt_cap_can_reach_untried_hosts_across_requests():
    clock = Clock()
    calls = []
    aliases = tuple(f"alt{i}.example.com" for i in range(6))

    def unavailable(url, timeout):
        calls.append(urlsplit(url).hostname)
        raise RuntimeError("watch_source_unavailable")

    roam = RoamingTransport(
        unavailable,
        equivalent_hosts={"one.example.com": aliases},
        clock=clock,
    )
    for count in (3, 6, 7):
        before = len(calls)
        with pytest.raises(RuntimeError, match="watch_source_unavailable"):
            roam("https://one.example.com/x", 5.0)
        assert len(calls) == count
        assert len(calls) - before <= MAX_ENDPOINT_ATTEMPTS
    assert calls == ["one.example.com", *aliases]
    with pytest.raises(RuntimeError, match="watch_endpoints_cooling_down"):
        roam("https://one.example.com/x", 5.0)
    assert len(calls) == 7


def test_duplicate_alias_never_spends_multiple_attempts_in_one_request():
    clock = Clock()
    calls = []

    def unavailable(url, timeout):
        calls.append(urlsplit(url).hostname)
        raise RuntimeError("watch_source_unavailable")

    roam = RoamingTransport(
        unavailable,
        equivalent_hosts={"one.example.com": ("alt.example.com", "alt.example.com")},
        clock=clock,
    )
    with pytest.raises(RuntimeError, match="watch_source_unavailable"):
        roam("https://one.example.com/x", 5.0)
    assert calls == ["one.example.com", "alt.example.com"]
