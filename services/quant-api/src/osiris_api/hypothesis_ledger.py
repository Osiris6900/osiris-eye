"""Hypothesis feedback ledger: the mesh checks its own predictions.

Every unusual-move hypothesis (from event-impact assessments or
``explain_move``) can be recorded in the node's local store. Once the
evaluation horizon has passed, the ledger measures what actually happened
— did the market keep moving the flagged way, reverse, or go nowhere? —
and keeps per-rule hit-rate statistics. Rules that chronically fail get
labelled ``noise`` so the network stops trusting them; rules that keep
working get labelled ``promising``.

Honesty notes: a "followed" outcome is follow-through of a past pattern,
never proof of cause. The ledger lives in its own local dataset
(``osiris.hypothesis_ledger``) — it is node bookkeeping, not a mesh
observation, so it does not pass through the p2p ingestion gates; the
underlying store stays immutable and append-only (outcomes are new rows,
never edits). Live trading remains disabled.
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Any

from .data_store import ResearchStore
from .event_market_impact import timestamped_returns_from_store
from .p2p_ingestion import DATASET

LEDGER_DATASET = "osiris.hypothesis_ledger"
LEDGER_SOURCE = "hypothesis-ledger"

DEFAULT_HORIZON_POINTS = 10
MAX_HORIZON_POINTS = 200
MIN_BASELINE_POINTS = 10
MIN_RATE_SAMPLES = 5
NOISE_HIT_RATE = 0.35
PROMISING_HIT_RATE = 0.60
MAX_RULE_LABEL_LENGTH = 48
MAX_SUMMARY_LENGTH = 240
MAX_INSTRUMENT_LENGTH = 64
MAX_LEDGER_ROWS = 1000


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_instant(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            raise ValueError("invalid_ledger_time")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid_ledger_time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("invalid_ledger_time")
    return parsed.astimezone(timezone.utc)


def _hypothesis_id(rule_label: str, instrument: str, direction: str,
                   created_iso: str, horizon_points: int) -> str:
    raw = f"{rule_label}|{instrument}|{direction}|{created_iso}|{horizon_points}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def record_hypothesis(
    store: ResearchStore,
    *,
    rule_label: str,
    instrument: str,
    direction: str,
    created: datetime | str | None = None,
    horizon_points: int = DEFAULT_HORIZON_POINTS,
    summary: str = "",
) -> dict[str, Any]:
    """Record one directional hypothesis for later evaluation.

    Idempotent: the same hypothesis (same rule, instrument, direction,
    creation instant and horizon) maps to one deterministic id; recording
    it twice reports ``duplicate`` instead of writing twice.
    """
    label = str(rule_label).strip()
    if not label or len(label) > MAX_RULE_LABEL_LENGTH:
        raise ValueError("invalid_rule_label")
    name = str(instrument).strip()
    if not name or len(name) > MAX_INSTRUMENT_LENGTH:
        raise ValueError("invalid_instrument")
    way = str(direction).strip().lower()
    if way not in ("up", "down"):
        raise ValueError("invalid_direction")
    if not 2 <= horizon_points <= MAX_HORIZON_POINTS:
        raise ValueError("invalid_horizon_points")
    created_dt = _coerce_instant(created) if created is not None else _utc_now()
    created_iso = created_dt.isoformat()
    hid = _hypothesis_id(label, name, way, created_iso, horizon_points)
    payload = {
        "rule_label": label,
        "instrument": name,
        "direction": way,
        "created_at": created_iso,
        "horizon_points": horizon_points,
        "summary": str(summary).strip()[:MAX_SUMMARY_LENGTH] or None,
        "status": "pending",
    }
    try:
        store.put(
            dataset=LEDGER_DATASET, source=LEDGER_SOURCE,
            record_key=f"HYPO:{hid}",
            event_time=created_iso, available_at=created_iso,
            payload=payload,
        )
    except ValueError as exc:
        if "duplicate" in str(exc):
            return {"hypothesis_id": hid, "recorded": False, "reason": "duplicate"}
        raise
    return {"hypothesis_id": hid, "recorded": True}


def record_from_assessment(
    store: ResearchStore,
    assessment: dict[str, Any],
    *,
    rule_label: str | None = None,
    created: datetime | str | None = None,
    horizon_points: int = DEFAULT_HORIZON_POINTS,
) -> dict[str, Any]:
    """Record every assessed, unusual/significant directional finding from
    an :func:`assess_event_impact` or :func:`aligned_event_impact` result.
    Non-directional or unflagged assessments are skipped, never forced."""
    if not isinstance(assessment, dict):
        raise ValueError("invalid_assessment")
    label = str(
        rule_label if rule_label is not None else assessment.get("event_kind", "")
    ).strip()
    if not label:
        raise ValueError("invalid_rule_label")
    created_default = created if created is not None else assessment.get("event_time")
    recorded: list[str] = []
    for item in assessment.get("assessments", []):
        if not isinstance(item, dict) or item.get("status") != "assessed":
            continue
        flagged = item.get("unusual_move") or item.get("significant")
        direction = str(item.get("direction", "")).strip().lower()
        if not flagged or direction not in ("up", "down"):
            continue
        outcome = record_hypothesis(
            store,
            rule_label=label,
            instrument=str(item.get("instrument", "")),
            direction=direction,
            created=created_default,
            horizon_points=horizon_points,
            summary=str(assessment.get("event_summary", "")),
        )
        if outcome["recorded"]:
            recorded.append(outcome["hypothesis_id"])
    return {"recorded": len(recorded), "hypothesis_ids": recorded}


def _ledger_rows(store: ResearchStore) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """(pending hypothesis payloads by id, ids that already have a RESULT)."""
    hypotheses: dict[str, dict[str, Any]] = {}
    evaluated: set[str] = set()
    for row in store.list(LEDGER_DATASET, limit=MAX_LEDGER_ROWS):
        record_key = str(row.get("record_key", ""))
        if not record_key.startswith("HYPO:"):
            continue
        hid = record_key[5:]
        if hid.endswith(":RESULT"):
            evaluated.add(hid[:-7])
            continue
        payload = row.get("payload")
        if isinstance(payload, dict):
            hypotheses[hid] = payload
    return hypotheses, evaluated


def _evaluate_one(store: ResearchStore, payload: dict[str, Any],
                  evaluated_at: datetime) -> dict[str, Any]:
    """Measure what the market did after one recorded hypothesis."""
    created_dt = _coerce_instant(payload.get("created_at"))
    horizon = int(payload.get("horizon_points", DEFAULT_HORIZON_POINTS))
    instrument = str(payload.get("instrument", ""))
    direction = str(payload.get("direction", ""))
    series_map = timestamped_returns_from_store(
        store, [instrument], dataset=DATASET, points=MAX_HORIZON_POINTS + MIN_BASELINE_POINTS + 8,
    )
    series = series_map.get(instrument, [])
    pre = [value for stamp, value in series
           if _coerce_instant(stamp) < created_dt]
    post = [value for stamp, value in series
            if _coerce_instant(stamp) >= created_dt]
    if len(post) < horizon:
        return {"status": "still_pending", "post_points": len(post),
                "required_post": horizon}
    if len(pre) < MIN_BASELINE_POINTS:
        return {"status": "evaluated", "outcome": "expired_insufficient_baseline",
                "pre_points": len(pre)}
    window = post[:horizon]
    pre_mean = sum(pre) / len(pre)
    pre_variance = sum((value - pre_mean) ** 2 for value in pre) / (len(pre) - 1)
    pre_std = math.sqrt(pre_variance)
    cumulative = 1.0
    for value in window:
        cumulative *= 1.0 + value
    cumulative -= 1.0
    band = pre_std * math.sqrt(len(window))  # random-walk expectation band
    followed = ((direction == "up" and cumulative > band)
                or (direction == "down" and cumulative < -band))
    reversed_move = ((direction == "up" and cumulative < -band)
                     or (direction == "down" and cumulative > band))
    outcome = "followed" if followed else "reversed" if reversed_move else "flat"
    return {
        "status": "evaluated",
        "outcome": outcome,
        "forward_cumulative_return": round(cumulative, 8),
        "expectation_band": round(band, 8),
        "pre_points": len(pre),
        "post_points_used": len(window),
        "evaluated_at": evaluated_at.isoformat(),
    }


def evaluate_due_hypotheses(
    store: ResearchStore,
    *,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Evaluate every pending hypothesis whose horizon has been reached.

    Writes one immutable ``HYPO:<id>:RESULT`` row per evaluated hypothesis
    (re-running later is a no-op for those). Hypotheses without enough
    post-creation market data stay pending; hypotheses without enough
    pre-creation baseline expire honestly.
    """
    evaluated_at = _coerce_instant(now) if now is not None else _utc_now()
    hypotheses, evaluated = _ledger_rows(store)
    counts = {"evaluated": 0, "still_pending": 0, "already_evaluated": 0}
    outcomes: list[dict[str, Any]] = []
    for hid in sorted(hypotheses):
        if hid in evaluated:
            counts["already_evaluated"] += 1
            continue
        result = _evaluate_one(store, hypotheses[hid], evaluated_at)
        if result["status"] == "still_pending":
            counts["still_pending"] += 1
            continue
        payload = {
            "hypothesis_id": hid,
            "rule_label": hypotheses[hid].get("rule_label"),
            "instrument": hypotheses[hid].get("instrument"),
            "direction": hypotheses[hid].get("direction"),
            **{key: value for key, value in result.items() if key != "status"},
        }
        try:
            store.put(
                dataset=LEDGER_DATASET, source=LEDGER_SOURCE,
                record_key=f"HYPO:{hid}:RESULT",
                event_time=evaluated_at.isoformat(),
                available_at=evaluated_at.isoformat(),
                payload=payload,
            )
        except ValueError as exc:
            if "duplicate" in str(exc):
                counts["already_evaluated"] += 1
                continue
            raise
        counts["evaluated"] += 1
        outcomes.append(payload)
    return {
        **counts,
        "outcomes": outcomes,
        "evaluated_at": evaluated_at.isoformat(),
        "live_execution": False,
    }


def rule_hit_rates(
    store: ResearchStore,
    *,
    min_samples: int = MIN_RATE_SAMPLES,
) -> dict[str, Any]:
    """Per-rule follow-through statistics from evaluated hypotheses.

    ``hit_rate`` = followed / (followed + reversed) over the recorded
    history; flat outcomes count as samples but not as hits or misses.
    Labels: ``noise`` (hit_rate below 0.35 with enough samples),
    ``promising`` (0.60+ with enough samples), ``mixed`` in between,
    ``insufficient_samples`` below ``min_samples`` scored outcomes. These
    are statistics of past patterns — never causality, never a guarantee.
    """
    if not 1 <= min_samples <= 100:
        raise ValueError("invalid_min_samples")
    hypotheses, evaluated = _ledger_rows(store)
    results: dict[str, dict[str, Any]] = {}
    for row in store.list(LEDGER_DATASET, limit=MAX_LEDGER_ROWS):
        record_key = str(row.get("record_key", ""))
        if not record_key.startswith("HYPO:") or not record_key.endswith(":RESULT"):
            continue
        payload = row.get("payload")
        if isinstance(payload, dict):
            results[record_key[5:-7]] = payload

    per_rule: dict[str, dict[str, int]] = {}
    for hid, result in results.items():
        hypothesis = hypotheses.get(hid)
        label = str((hypothesis or result).get("rule_label", "?"))
        stats = per_rule.setdefault(
            label, {"followed": 0, "reversed": 0, "flat": 0,
                    "expired": 0, "pending": 0},
        )
        outcome = str(result.get("outcome", ""))
        if outcome in ("followed", "reversed", "flat"):
            stats[outcome] += 1
        else:
            stats["expired"] += 1
    for hid, hypothesis in hypotheses.items():
        if hid not in results:
            label = str(hypothesis.get("rule_label", "?"))
            per_rule.setdefault(
                label, {"followed": 0, "reversed": 0, "flat": 0,
                        "expired": 0, "pending": 0},
            )["pending"] += 1

    report: dict[str, Any] = {}
    for label in sorted(per_rule):
        stats = per_rule[label]
        scored = stats["followed"] + stats["reversed"]
        hit_rate = round(stats["followed"] / scored, 4) if scored else None
        if scored < min_samples:
            trust = "insufficient_samples"
        elif hit_rate is not None and hit_rate < NOISE_HIT_RATE:
            trust = "noise"
        elif hit_rate is not None and hit_rate >= PROMISING_HIT_RATE:
            trust = "promising"
        else:
            trust = "mixed"
        report[label] = {**stats, "scored": scored, "hit_rate": hit_rate,
                         "trust": trust}
    return {
        "rules": report,
        "min_samples": min_samples,
        "note": ("follow-through statistics of past hypotheses; "
                 "not causality and not a guarantee"),
        "live_execution": False,
    }
