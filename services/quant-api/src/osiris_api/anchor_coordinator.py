"""Anchor coverage: the mesh coordinates who fetches — everyone receives.

A hundred nodes all calling Binance is a hundred times the same question.
The anchor model ends that: per source, the nodes best placed to fetch
become **anchors**; everyone else goes **standby** and simply listens to
the gossip. For the standby node the dream is literally true — it never
touches an API, yet the world's data flows in.

The trust rule that makes it honest:

- **Fresh quotes are proof; announcements are only hints.** A peer
  claiming "I cover Binance" earns nothing. A peer's signed quote that
  arrived 20 seconds ago earns this node's standby. No fresh data, no
  standby — the node fetches itself. A lying peer can never blind the
  mesh; it can only make nodes fetch more, never less.
- **Doubt fetches.** Any ambiguity — peer entering the grace band, no
  record of who fetched what — resolves toward anchoring. The worst
  failure mode of this design is a redundant public request, never a
  blind node.
- **Latency is compared honestly.** The plan reports each peer's
  measured latency (stamped into its gossiped payloads) against this
  node's own, so "who should anchor" is a measurement, not a claim.

Duties per source: ``anchor`` (this node fetches), ``standby`` (fresh
peer coverage proven — listen only), ``covered_by_peer`` (this node
cannot fetch the source but a peer provably does), or ``blind`` (no one
provably covers it — surfaced, never hidden).

Live trading remains disabled.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Any

from .data_store import ResearchStore
from .global_market_watch import _now_utc
from .p2p_ingestion import DATASET

MIN_FRESHNESS_SECONDS = 5.0
MAX_FRESHNESS_SECONDS = 3600.0
DEFAULT_FRESHNESS_SECONDS = 60.0
MAX_GRACE_SECONDS = 600.0
DEFAULT_GRACE_SECONDS = 15.0
_MAX_SCAN_ROWS = 1000  # store.list's own upper bound

_SOURCE_PREFIX = "p2p:"
_ANNOUNCE_PROVIDER = "node-anchor-announce"


def _parse_instant(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        instant = datetime.fromisoformat(value)
    except ValueError:
        return None
    return instant if instant.tzinfo is not None else None


def _peer_latency_ms(inner: dict[str, Any]) -> float | None:
    """The fetching node's measured distance to the venue, if stamped."""
    for key in ("fetch_roundtrip_ms", "venue_event_age_ms"):
        value = inner.get(key)
        if isinstance(value, int | float) and not math.isnan(value):
            return float(value)
    return None


def peer_coverage_from_store(
    store: ResearchStore,
    providers: set[str] | frozenset[str],
    *,
    own_record_keys: set[str] | frozenset[str] | None = None,
    now: datetime | None = None,
    dataset: str = DATASET,
) -> dict[str, dict[str, Any]]:
    """Latest proven peer quote per provider — proof, not promises.

    Scans the immutable store newest-first and keeps, for each provider,
    the newest row that this node did **not** fetch itself
    (``own_record_keys``). A quote only counts as peer coverage when the
    node can tell it came from somewhere else.
    """
    current = now or _now_utc()
    own = set(own_record_keys or ())
    wanted = set(providers)
    coverage: dict[str, dict[str, Any]] = {}
    for row in store.list(dataset, limit=_MAX_SCAN_ROWS):
        source = str(row.get("source", ""))
        if not source.startswith(_SOURCE_PREFIX):
            continue
        provider = source[len(_SOURCE_PREFIX) :]
        if provider not in wanted or provider in coverage:
            continue
        record_key = str(row.get("record_key", ""))
        if record_key in own:
            continue  # our own fetch proves nothing about peers
        event_time = _parse_instant(row.get("event_time"))
        if event_time is None:
            continue
        latency_ms = None
        try:
            inner = json.loads(row["payload"]["payload"])
            if isinstance(inner, dict):
                latency_ms = _peer_latency_ms(inner)
        except (KeyError, TypeError, ValueError):
            latency_ms = None
        coverage[provider] = {
            "record_key": record_key,
            "event_time": event_time.isoformat(),
            "age_seconds": round((current - event_time).total_seconds(), 3),
            "peer_latency_ms": latency_ms,
        }
        if len(coverage) == len(wanted):
            break
    return coverage


def coverage_announcement(
    provider: str,
    instruments: tuple[str, ...] | list[str],
    *,
    measured_latency_ms: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """A hint for peers: "I anchor this source, here is my distance."

    Signed like every observation, but an announcement is never proof —
    peers go standby only on fresh quotes. Ingestion still requires the
    operator's allowlist, exactly like any other provider.
    """
    if not provider or not provider.strip():
        raise ValueError("invalid_announce_provider")
    if not instruments:
        raise ValueError("empty_announce_instruments")
    stamp = now or _now_utc()
    body: dict[str, Any] = {
        "announce": "anchor_coverage",
        "covers_provider": provider.strip(),
        "instruments": sorted(str(item) for item in instruments),
        "proof_rule": "standby_requires_fresh_quotes_not_announcements",
    }
    if measured_latency_ms is not None:
        if not isinstance(measured_latency_ms, int | float) or math.isnan(measured_latency_ms):
            raise ValueError("invalid_announce_latency")
        body["measured_latency_ms"] = float(measured_latency_ms)
    payload = json.dumps(body, allow_nan=False, separators=(",", ":"), sort_keys=True)
    return {
        "schema": "osiris.observation.v1",
        "instrument": f"ANCHOR:{provider.strip()}",
        "observed_at": stamp.isoformat(),
        "provider": _ANNOUNCE_PROVIDER,
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


def anchor_plan(
    store: ResearchStore,
    *,
    own_fetchable: dict[str, tuple[str, ...]],
    own_record_keys: set[str] | frozenset[str] | None = None,
    own_latency_ms: dict[str, float] | None = None,
    watched: set[str] | frozenset[str] | None = None,
    previous_duties: dict[str, str] | None = None,
    freshness_seconds: float = DEFAULT_FRESHNESS_SECONDS,
    grace_seconds: float = DEFAULT_GRACE_SECONDS,
    now: datetime | None = None,
    dataset: str = DATASET,
) -> dict[str, Any]:
    """Decide, per source, whether this node fetches or listens.

    ``own_fetchable`` maps providers this node *can* fetch to their
    instruments. ``watched`` may add providers the node cares about but
    cannot fetch — those resolve to ``covered_by_peer`` or ``blind``.
    ``previous_duties`` adds hysteresis: inside the grace band a duty
    holds its previous state, so a peer hovering near the boundary never
    makes the node flap between fetching and listening.
    """
    if not MIN_FRESHNESS_SECONDS <= freshness_seconds <= MAX_FRESHNESS_SECONDS:
        raise ValueError("invalid_freshness_seconds")
    if not 0.0 <= grace_seconds <= MAX_GRACE_SECONDS:
        raise ValueError("invalid_grace_seconds")
    if not own_fetchable:
        raise ValueError("empty_own_fetchable")
    current = now or _now_utc()
    previous = previous_duties or {}
    own_latency = own_latency_ms or {}
    sources = set(own_fetchable) | set(watched or ())
    coverage = peer_coverage_from_store(
        store,
        sources,
        own_record_keys=own_record_keys,
        now=current,
        dataset=dataset,
    )

    plan: dict[str, dict[str, Any]] = {}
    for provider in sorted(sources):
        fetchable = provider in own_fetchable
        proof = coverage.get(provider)
        entry: dict[str, Any] = {
            "fetchable": fetchable,
            "peer_age_seconds": proof["age_seconds"] if proof else None,
            "peer_latency_ms": proof["peer_latency_ms"] if proof else None,
            "own_latency_ms": own_latency.get(provider),
        }
        # Rows in the store already passed ingestion's future-skew gate,
        # so a small negative age (peer clock slightly ahead) is just fresh.
        if proof is None:
            entry["duty"] = "anchor" if fetchable else "blind"
            entry["reason"] = "no_peer_coverage" if fetchable else "cannot_fetch_no_coverage"
        elif proof["age_seconds"] <= freshness_seconds:
            entry["duty"] = "standby" if fetchable else "covered_by_peer"
            entry["reason"] = "peer_fresh" if fetchable else "cannot_fetch_peer_fresh"
        elif proof["age_seconds"] <= freshness_seconds + grace_seconds:
            # Grace band: hold the previous duty when one is known;
            # when in doubt, fetch — doubt never listens.
            held = previous.get(provider)
            if fetchable and held == "standby":
                entry["duty"] = "standby"
                entry["reason"] = "grace_band_hold_standby"
            elif not fetchable:
                entry["duty"] = "covered_by_peer"
                entry["reason"] = "cannot_fetch_peer_in_grace"
            else:
                entry["duty"] = "anchor"
                entry["reason"] = "grace_band_doubt_fetches"
        else:
            entry["duty"] = "anchor" if fetchable else "blind"
            entry["reason"] = "peer_stale_failover" if fetchable else "cannot_fetch_no_coverage"
        plan[provider] = entry

    duties = [entry["duty"] for entry in plan.values()]
    return {
        "cycle": "anchor_plan",
        "freshness_seconds": freshness_seconds,
        "grace_seconds": grace_seconds,
        "plan": plan,
        "summary": {
            "sources": len(plan),
            "anchors": duties.count("anchor"),
            "standby": duties.count("standby"),
            "covered_by_peer": duties.count("covered_by_peer"),
            "blind": [provider for provider, entry in plan.items() if entry["duty"] == "blind"],
            "api_calls_saved": duties.count("standby"),
        },
        "proof_rule": "standby_requires_fresh_quotes_not_announcements",
        "co_location": False,
        "live_execution": False,
        "broker_order_submitted": False,
    }
