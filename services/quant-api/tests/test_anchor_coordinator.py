"""Tests for the anchor coordinator: who fetches, who listens, who is blind.

No network: quotes enter through the real fail-closed ingestion gates,
and the plan is computed against a fixed clock. The trust rule under
test everywhere: fresh quotes are proof, announcements are only hints,
and doubt always fetches.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from osiris_api.anchor_coordinator import (
    DEFAULT_FRESHNESS_SECONDS,
    DEFAULT_GRACE_SECONDS,
    MAX_FRESHNESS_SECONDS,
    MAX_GRACE_SECONDS,
    MIN_FRESHNESS_SECONDS,
    anchor_plan,
    coverage_announcement,
    peer_coverage_from_store,
)
from osiris_api.data_store import ResearchStore
from osiris_api.global_market_watch import _observation
from osiris_api.p2p_ingestion import DATASET, ENABLE_ENV, ingest_p2p_observation

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)
BINANCE = "binance-public"
OKX = "okx-public"
WAZIRX = "wazirx-public"  # example of a source this node cannot fetch


@pytest.fixture
def store(tmp_path):
    return ResearchStore(str(tmp_path / "anchor.sqlite3"))


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")


def _quote(provider, instrument="BTCUSDT", price=69001.0, when=None, latency_ms=None):
    body = {"price": price}
    if latency_ms is not None:
        body["fetch_roundtrip_ms"] = latency_ms
    return _observation(instrument, provider, body, when or NOW)


def _inject(store, observation, *, max_age=10_000.0, skew=120.0):
    provider = observation["provider"]
    ingest_p2p_observation(
        store,
        observation,
        allowed_providers={provider},
        max_age_seconds=max_age,
        max_future_skew_seconds=skew,
        now=NOW,
    )


def _plan(store, **kwargs):
    kwargs.setdefault("own_fetchable", {BINANCE: ("BTCUSDT",), OKX: ("BTCUSDT",)})
    kwargs.setdefault("now", NOW)
    return anchor_plan(store, **kwargs)


# -------------------------------------------------------------- proof, not promises


def test_no_peer_coverage_everything_anchors(store, enabled):
    result = _plan(store)
    assert result["summary"]["anchors"] == 2
    assert result["summary"]["standby"] == 0
    assert result["summary"]["api_calls_saved"] == 0
    for entry in result["plan"].values():
        assert entry["duty"] == "anchor"
        assert entry["reason"] == "no_peer_coverage"


def test_fresh_peer_quote_means_standby(store, enabled):
    _inject(store, _quote(BINANCE, when=NOW - timedelta(seconds=20), latency_ms=42.0))
    result = _plan(store)
    assert result["plan"][BINANCE]["duty"] == "standby"
    assert result["plan"][BINANCE]["reason"] == "peer_fresh"
    assert result["plan"][BINANCE]["peer_age_seconds"] == pytest.approx(20.0)
    assert result["plan"][BINANCE]["peer_latency_ms"] == 42.0
    assert result["plan"][OKX]["duty"] == "anchor"  # independent per source
    assert result["summary"]["api_calls_saved"] == 1


def test_stale_peer_quote_triggers_failover(store, enabled):
    _inject(store, _quote(BINANCE, when=NOW - timedelta(seconds=300)))
    result = _plan(store)
    assert result["plan"][BINANCE]["duty"] == "anchor"
    assert result["plan"][BINANCE]["reason"] == "peer_stale_failover"


def test_announcement_without_quotes_never_earns_standby(store, enabled):
    announcement = coverage_announcement(BINANCE, ("BTCUSDT",), measured_latency_ms=5.0, now=NOW)
    _inject(store, announcement)
    result = _plan(store)
    assert result["plan"][BINANCE]["duty"] == "anchor"
    assert result["plan"][BINANCE]["reason"] == "no_peer_coverage"
    assert result["proof_rule"] == "standby_requires_fresh_quotes_not_announcements"


def test_own_fetch_never_counts_as_peer_coverage(store, enabled):
    observation = _quote(BINANCE, when=NOW - timedelta(seconds=10))
    _inject(store, observation)
    record_key = f"BTCUSDT:{observation['payload_sha256'][:32]}"
    result = _plan(store, own_record_keys={record_key})
    assert result["plan"][BINANCE]["duty"] == "anchor"
    assert result["plan"][BINANCE]["reason"] == "no_peer_coverage"


def test_future_skew_within_ingest_gate_counts_as_fresh(store, enabled):
    _inject(store, _quote(BINANCE, when=NOW + timedelta(seconds=5)))
    result = _plan(store)
    assert result["plan"][BINANCE]["duty"] == "standby"  # peer clock slightly ahead


# -------------------------------------------------------------- grace band hysteresis


def test_grace_band_holds_previous_standby(store, enabled):
    age = DEFAULT_FRESHNESS_SECONDS + DEFAULT_GRACE_SECONDS / 2
    _inject(store, _quote(BINANCE, when=NOW - timedelta(seconds=age)))
    result = _plan(store, previous_duties={BINANCE: "standby"})
    assert result["plan"][BINANCE]["duty"] == "standby"
    assert result["plan"][BINANCE]["reason"] == "grace_band_hold_standby"


def test_grace_band_without_history_doubt_fetches(store, enabled):
    age = DEFAULT_FRESHNESS_SECONDS + DEFAULT_GRACE_SECONDS / 2
    _inject(store, _quote(BINANCE, when=NOW - timedelta(seconds=age)))
    result = _plan(store, previous_duties={BINANCE: "anchor"})
    assert result["plan"][BINANCE]["duty"] == "anchor"
    assert result["plan"][BINANCE]["reason"] == "grace_band_doubt_fetches"


# -------------------------------------------------------------- sources we cannot fetch


def test_unfetchable_source_with_fresh_peer_is_covered(store, enabled):
    _inject(store, _quote(WAZIRX, instrument="BTCINR", when=NOW - timedelta(seconds=30)))
    result = _plan(store, watched={WAZIRX})
    assert result["plan"][WAZIRX]["duty"] == "covered_by_peer"
    assert result["plan"][WAZIRX]["reason"] == "cannot_fetch_peer_fresh"
    assert result["plan"][WAZIRX]["fetchable"] is False


def test_unfetchable_source_without_peer_is_blind(store, enabled):
    result = _plan(store, watched={WAZIRX})
    assert result["plan"][WAZIRX]["duty"] == "blind"
    assert result["plan"][WAZIRX]["reason"] == "cannot_fetch_no_coverage"
    assert result["summary"]["blind"] == [WAZIRX]  # blindness surfaced, never hidden


# -------------------------------------------------------------- peer coverage scan


def test_peer_coverage_reads_stream_latency_and_skips_malformed(store, enabled):
    _inject(store, _quote(BINANCE, when=NOW - timedelta(seconds=12), latency_ms=55.0))
    coverage = peer_coverage_from_store(store, {BINANCE, OKX}, now=NOW)
    assert coverage[BINANCE]["peer_latency_ms"] == 55.0
    assert coverage[BINANCE]["age_seconds"] == pytest.approx(12.0)
    assert OKX not in coverage


def test_peer_coverage_takes_newest_quote_per_provider(store, enabled):
    _inject(store, _quote(BINANCE, when=NOW - timedelta(seconds=300), price=1.0))
    _inject(store, _quote(BINANCE, when=NOW - timedelta(seconds=8), price=2.0))
    result = _plan(store)
    assert result["plan"][BINANCE]["duty"] == "standby"
    assert result["plan"][BINANCE]["peer_age_seconds"] == pytest.approx(8.0)


# -------------------------------------------------------------- announcements


def test_coverage_announcement_is_signed_and_sorted():
    announcement = coverage_announcement(
        BINANCE,
        ("ETHUSDT", "BTCUSDT"),
        measured_latency_ms=7.5,
        now=NOW,
    )
    assert announcement["schema"] == "osiris.observation.v1"
    assert announcement["instrument"] == f"ANCHOR:{BINANCE}"
    assert announcement["provider"] == "node-anchor-announce"
    assert announcement["observed_at"] == NOW.isoformat()
    assert len(announcement["payload_sha256"]) == 64
    body = json.loads(announcement["payload"])
    assert body["announce"] == "anchor_coverage"
    assert body["covers_provider"] == BINANCE
    assert body["instruments"] == ["BTCUSDT", "ETHUSDT"]
    assert body["measured_latency_ms"] == 7.5
    assert body["proof_rule"] == "standby_requires_fresh_quotes_not_announcements"


def test_coverage_announcement_validates_inputs():
    with pytest.raises(ValueError, match="invalid_announce_provider"):
        coverage_announcement("  ", ("BTCUSDT",))
    with pytest.raises(ValueError, match="empty_announce_instruments"):
        coverage_announcement(BINANCE, ())
    with pytest.raises(ValueError, match="invalid_announce_latency"):
        coverage_announcement(BINANCE, ("BTCUSDT",), measured_latency_ms=float("nan"))


# -------------------------------------------------------------- shape and bounds


def test_result_shape_and_honest_flags(store, enabled):
    result = _plan(store)
    assert result["cycle"] == "anchor_plan"
    assert result["freshness_seconds"] == DEFAULT_FRESHNESS_SECONDS
    assert result["grace_seconds"] == DEFAULT_GRACE_SECONDS
    assert result["co_location"] is False
    assert result["live_execution"] is False
    assert result["broker_order_submitted"] is False
    assert set(result["summary"]) == {
        "sources",
        "anchors",
        "standby",
        "covered_by_peer",
        "blind",
        "api_calls_saved",
    }


def test_bounds_constants_are_honest():
    assert MIN_FRESHNESS_SECONDS == 5.0
    assert MAX_FRESHNESS_SECONDS == 3600.0
    assert MAX_GRACE_SECONDS == 600.0


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"freshness_seconds": 1.0}, "invalid_freshness_seconds"),
        ({"freshness_seconds": 4000.0}, "invalid_freshness_seconds"),
        ({"grace_seconds": -1.0}, "invalid_grace_seconds"),
        ({"grace_seconds": 601.0}, "invalid_grace_seconds"),
        ({"own_fetchable": {}}, "empty_own_fetchable"),
    ],
)
def test_invalid_arguments_raise(store, override, match):
    kwargs = {"own_fetchable": {BINANCE: ("BTCUSDT",)}, "now": NOW}
    kwargs.update(override)
    with pytest.raises(ValueError, match=match):
        anchor_plan(store, **kwargs)


def test_store_stays_ingestion_gated(store, enabled, monkeypatch):
    """The coordinator reads only what the gates let in — a rejected
    quote is invisible to the plan, exactly as it should be."""
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    with pytest.raises(ValueError):
        _inject(store, _quote(BINANCE))
    result = _plan(store)
    assert result["plan"][BINANCE]["duty"] == "anchor"  # gated quote never happened
    assert store.list(DATASET, limit=5) == []
