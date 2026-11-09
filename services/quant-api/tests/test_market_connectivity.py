"""Offline market-connectivity policy tests. No sockets and no exchange calls."""
import io
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.utils import format_datetime
from urllib.error import HTTPError, URLError

import pytest

from osiris_api.global_market_watch import _http_get
from osiris_api.market_connectivity import (
    MAX_RETRY_AFTER_SECONDS,
    MIN_RETRY_AFTER_SECONDS,
    RateLimited,
    classify_transport_failure,
    endpoint_health_score,
    jittered_reconnect_delay,
    retry_after_seconds,
)
from osiris_api.roaming_watch import MAX_COOLDOWN_SECONDS

NOW = datetime(2026, 11, 9, 18, 0, tzinfo=timezone.utc)


def _http_error(status: int, retry_after: str | None = None) -> HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return HTTPError("https://example.test/quote", status, "limited", headers, io.BytesIO(b""))


def test_retry_after_parses_delta_http_date_and_garbage():
    assert retry_after_seconds("12") == 12.0
    assert retry_after_seconds("0") == MIN_RETRY_AFTER_SECONDS
    assert retry_after_seconds("-4") == MIN_RETRY_AFTER_SECONDS
    assert retry_after_seconds("999999") == MAX_RETRY_AFTER_SECONDS
    assert retry_after_seconds(None) == 60.0
    assert retry_after_seconds("not-a-delay") == 60.0
    future = format_datetime(NOW + timedelta(seconds=30), usegmt=True)
    assert retry_after_seconds(future, now=NOW) == 30.0
    past = format_datetime(NOW - timedelta(seconds=15), usegmt=True)
    assert retry_after_seconds(past, now=NOW) == MIN_RETRY_AFTER_SECONDS
    assert MAX_RETRY_AFTER_SECONDS == MAX_COOLDOWN_SECONDS


def test_classify_transport_failure_splits_rate_limits_from_outages():
    limited = classify_transport_failure(_http_error(429, "15"))
    assert isinstance(limited, RateLimited)
    assert limited.status == 429
    assert limited.retry_after_seconds == 15.0
    banned = classify_transport_failure(_http_error(418))
    assert isinstance(banned, RateLimited)
    assert banned.status == 418
    assert banned.retry_after_seconds == 60.0
    down = classify_transport_failure(_http_error(503))
    assert type(down) is RuntimeError
    assert str(down) == "watch_source_unavailable"
    unreachable = classify_transport_failure(URLError("dns"))
    assert str(unreachable) == "watch_source_unavailable"
    with pytest.raises(ValueError, match="invalid_retry_after"):
        RateLimited(0)
    with pytest.raises(ValueError, match="invalid_rate_limit_status"):
        RateLimited(5, status=500)


def test_http_get_maps_429_without_opening_a_real_socket(monkeypatch):
    def boom(request, timeout):
        raise _http_error(429, "12")

    monkeypatch.setattr("osiris_api.global_market_watch.urlopen", boom)
    with pytest.raises(RateLimited) as caught:
        _http_get("https://example.test/quote", 1.0)
    assert caught.value.retry_after_seconds == 12.0
    assert caught.value.status == 429


def test_http_get_maps_timeouts_to_unavailable(monkeypatch):
    def boom(request, timeout):
        raise TimeoutError("slow")

    monkeypatch.setattr("osiris_api.global_market_watch.urlopen", boom)
    with pytest.raises(RuntimeError, match="watch_source_unavailable"):
        _http_get("https://example.test/quote", 1.0)


def test_jittered_reconnect_delay_is_deterministic_bounded_and_spread():
    first = jittered_reconnect_delay(1, material="okx-public")
    again = jittered_reconnect_delay(1, material="okx-public")
    other = jittered_reconnect_delay(1, material="bybit-public")
    assert first == again
    assert first != other
    assert 0.5 <= first <= 1.0
    assert 0.5 <= other <= 1.0
    capped = jittered_reconnect_delay(8, material="okx-public")
    assert 15.0 <= capped <= 30.0
    # Same attempt, different venues, every sample stays inside its band.
    samples = [jittered_reconnect_delay(3, material=f"venue-{index}") for index in range(20)]
    assert min(samples) >= 2.0 and max(samples) <= 4.0
    assert len(set(samples)) > 1
    with pytest.raises(ValueError, match="invalid_reconnect_attempt"):
        jittered_reconnect_delay(0, material="okx-public")
    with pytest.raises(ValueError, match="invalid_reconnect_material"):
        jittered_reconnect_delay(1, material=" ")


def test_health_score_prefers_fast_known_good_over_untried_over_known_bad():
    unknown = endpoint_health_score({"successes": 0, "failures": 0, "ewma_rtt_ms": None})
    fast = endpoint_health_score({"successes": 4, "failures": 0, "ewma_rtt_ms": 40.0})
    slow = endpoint_health_score({"successes": 4, "failures": 0, "ewma_rtt_ms": 400.0})
    flaky = endpoint_health_score({"successes": 1, "failures": 3, "ewma_rtt_ms": 40.0})
    dead = endpoint_health_score({"successes": 0, "failures": 5, "ewma_rtt_ms": None})
    assert dead == 0.0
    assert dead < unknown == 0.5
    assert unknown < flaky < slow < fast
    assert fast <= 1.0
    with pytest.raises(ValueError, match="invalid_endpoint_counters"):
        endpoint_health_score({"successes": True, "failures": 0})
