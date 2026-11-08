"""Autonomous research cycle: the mesh reviews its own store.

The watchers (market, world, celestial) fill the node's immutable
observation store; the intelligence, aligned-impact and ledger layers
measure it. This module closes the loop in one local pass:

1. find recent world/celestial events among the stored observations,
2. measure each event's event-time aligned market impact (see
   :func:`event_market_impact.aligned_event_impact`),
3. record every significant directional finding into the hypothesis
   ledger (see :mod:`hypothesis_ledger`),
4. evaluate recorded hypotheses whose horizon has passed,
5. report per-rule follow-through statistics and trust labels.

Everything runs against the node's own store — no network call, no
broadcast, nothing fabricated. All findings stay honest hypotheses:
a significant reaction or a "followed" outcome is a pattern statistic,
never proof of cause. Live trading remains disabled.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from .data_store import ResearchStore
from .event_market_impact import (
    DEFAULT_EXPOSURE_RULES,
    ExposureRule,
    aligned_event_impact,
)
from .hypothesis_ledger import (
    evaluate_due_hypotheses,
    record_from_assessment,
    rule_hit_rates,
)
from .p2p_ingestion import DATASET

#: record_key prefixes that carry world/celestial/ocean events. Market quotes
#: use bare symbols (``^SPX:...``, ``BTCUSDT:...``) and are never scanned
#: as events.
EVENT_INSTRUMENT_PREFIXES = (
    "EQ:", "EONET:", "WX:", "ASTRO:", "SWPC:",
    "SEA:", "BUOY:", "TIDE:", "ALRT:", "JMA:", "OCEAN:",
    "RIV:", "RAD:", "SAT:", "VOL:",
)

DEFAULT_LOOKBACK_HOURS = 72
MAX_LOOKBACK_HOURS = 24 * 31  # about one month
DEFAULT_CYCLE_EVENTS = 24
MAX_CYCLE_EVENTS = 100
MAX_STORE_ROWS = 1000
#: Events stamped slightly in the future (clock skew) still count;
#: anything beyond this has simply not happened yet.
FUTURE_SKEW_SECONDS = 300.0


def _coerce_now(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            raise ValueError("invalid_cycle_time")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid_cycle_time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("invalid_cycle_time")
    return parsed.astimezone(timezone.utc)


def _parse_instant(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _event_from_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a world/celestial event payload from one store row.

    Returns ``None`` for market quotes, malformed rows and non-event
    payloads — scanning skips them, it never fails the cycle.
    """
    record_key = str(row.get("record_key", ""))
    if not record_key.startswith(EVENT_INSTRUMENT_PREFIXES):
        return None
    stored = row.get("payload")
    if not isinstance(stored, dict):
        return None
    raw = stored.get("payload")
    if not isinstance(raw, str):
        return None
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    if not str(event.get("kind", "")).strip():
        return None
    if _parse_instant(event.get("event_time")) is None:
        return None
    return event


def recent_events_from_store(
    store: ResearchStore,
    *,
    now: datetime | str | None = None,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    max_events: int = DEFAULT_CYCLE_EVENTS,
    dataset: str = DATASET,
) -> list[dict[str, Any]]:
    """Recent world/celestial event payloads, newest first.

    Reads the immutable observations dataset newest-first, keeps rows
    whose record_key carries a world/celestial prefix and whose payload
    parses to an event with a kind and an aware event_time inside the
    lookback window (and not beyond a small future skew). Repeated
    observations of the same event (same kind, event_time and place —
    e.g. updated magnitude reports) collapse to the newest one.
    """
    current = _coerce_now(now)
    if not 1 <= lookback_hours <= MAX_LOOKBACK_HOURS:
        raise ValueError("invalid_lookback_hours")
    if not 1 <= max_events <= MAX_CYCLE_EVENTS:
        raise ValueError("invalid_max_events")
    cutoff = current - timedelta(hours=lookback_hours)
    latest_allowed = current + timedelta(seconds=FUTURE_SKEW_SECONDS)

    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in store.list(dataset, limit=MAX_STORE_ROWS):
        event = _event_from_row(row)
        if event is None:
            continue
        when = _parse_instant(event.get("event_time"))
        if when is None or when < cutoff or when > latest_allowed:
            continue
        summary = str(event.get("place") or event.get("title") or event.get("city") or "")
        key = (str(event.get("kind", "")), when.isoformat(), summary)
        if key in seen:
            continue
        seen.add(key)
        events.append(event)
        if len(events) >= max_events:
            break
    return events


def run_research_cycle(
    store: ResearchStore,
    *,
    now: datetime | str | None = None,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    max_events: int = DEFAULT_CYCLE_EVENTS,
    rules: tuple[ExposureRule, ...] = DEFAULT_EXPOSURE_RULES,
    horizon_points: int = 10,
    min_samples: int = 5,
    dataset: str = DATASET,
) -> dict[str, Any]:
    """One autonomous research pass over the node's own store.

    Assesses every recent world/celestial event with event-time aligned
    impact, records significant directional findings as ledger hypotheses
    (idempotent — re-running the same cycle records nothing twice),
    evaluates hypotheses whose horizon has passed and aggregates the
    per-rule trust labels. Bounded: at most ``max_events`` events per
    pass, all store reads capped. The report always carries
    ``causality: "not_established"`` and ``live_execution: False``.
    """
    current = _coerce_now(now)
    if not 2 <= horizon_points <= 200:
        raise ValueError("invalid_horizon_points")
    if not 1 <= min_samples <= 100:
        raise ValueError("invalid_min_samples")
    events = recent_events_from_store(
        store, now=current, lookback_hours=lookback_hours,
        max_events=max_events, dataset=dataset,
    )

    verdicts: dict[str, int] = {}
    assessed_events: list[dict[str, Any]] = []
    hypotheses_recorded = 0
    skipped_invalid = 0
    for event in events:
        try:
            assessment = aligned_event_impact(
                store, event, rules=rules, dataset=dataset,
            )
        except ValueError:
            skipped_invalid += 1
            continue
        verdicts[assessment["verdict"]] = verdicts.get(assessment["verdict"], 0) + 1
        assessed_events.append({
            "event_kind": assessment["event_kind"],
            "event_summary": assessment["event_summary"],
            "event_time": assessment["event_time"],
            "verdict": assessment["verdict"],
        })
        recorded = record_from_assessment(
            store, assessment, horizon_points=horizon_points,
        )
        hypotheses_recorded += recorded["recorded"]

    evaluations = evaluate_due_hypotheses(store, now=current)
    outcome_tally: dict[str, int] = {}
    for outcome in evaluations["outcomes"]:
        name = str(outcome.get("outcome", "?"))
        outcome_tally[name] = outcome_tally.get(name, 0) + 1

    rates = rule_hit_rates(store, min_samples=min_samples)
    trust_tally: dict[str, int] = {}
    for rule_report in rates["rules"].values():
        label = str(rule_report.get("trust", "?"))
        trust_tally[label] = trust_tally.get(label, 0) + 1

    return {
        "cycle": "research_cycle",
        "ran_at": current.isoformat(),
        "lookback_hours": lookback_hours,
        "events_scanned": len(events),
        "events_skipped_invalid": skipped_invalid,
        "verdicts": verdicts,
        "assessed_events": assessed_events,
        "hypotheses_recorded": hypotheses_recorded,
        "evaluations": {
            "evaluated": evaluations["evaluated"],
            "still_pending": evaluations["still_pending"],
            "already_evaluated": evaluations["already_evaluated"],
        },
        "outcomes": outcome_tally,
        "trust_labels": trust_tally,
        "rules": rates["rules"],
        "hypothesis": True,
        "causality": "not_established",
        "note": (
            "event-aligned reactions and follow-through statistics of "
            "past hypotheses; not causality and not a guarantee"
        ),
        "live_execution": False,
    }
