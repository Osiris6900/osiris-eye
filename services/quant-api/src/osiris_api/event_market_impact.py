"""Event → market impact hypotheses.

The world-event watch layer observes everything (earthquakes, storms,
volcanoes, wildfires, ice, city weather). This module answers the follow-up
question for each observed event: which instruments could this plausibly
touch, and did any of them actually move in an unusual way?

Method, honestly labelled: an operator-extensible exposure map links event
kind + location keywords to instruments; each exposed instrument's latest
return is then checked with the mesh_market_intelligence primitives
(z-score vs baseline, regime, leading context). The output is always a
hypothesis — correlation patterns around an event never establish that the
event caused the move. Live trading remains disabled.
"""
from __future__ import annotations

import json
from typing import Any, NamedTuple

from .data_store import ResearchStore
from .mesh_market_intelligence import explain_move, quote_value, returns_series
from .p2p_ingestion import DATASET

MAX_EXPOSED_INSTRUMENTS = 16
MAX_ASSESSMENTS = 8
MAX_RETURNS_POINTS = 512
MAX_STORE_ROWS = 1000
MAX_INSTRUMENT_LENGTH = 64


class ExposureRule(NamedTuple):
    """One exposure rule: an event of ``kind`` whose descriptive text
    contains any of ``keywords`` plausibly touches ``instruments``.

    An empty ``keywords`` tuple matches every event of that kind (use
    sparingly — broad rules dilute the signal).
    """
    kind: str
    keywords: tuple[str, ...]
    instruments: tuple[str, ...]


#: Default exposure map. Operators can extend it; instruments deliberately
#: mirror the keyless market watchlist so impact can always be checked.
DEFAULT_EXPOSURE_RULES: tuple[ExposureRule, ...] = (
    ExposureRule("earthquake", ("japan", "tokyo", "osaka"), ("^NKX", "USD-JPY")),
    ExposureRule("earthquake", ("california", "san francisco", "los angeles", "alaska", "hawaii"),
                 ("^SPX", "^NDQ", "^DJI")),
    ExposureRule("earthquake", ("india", "delhi", "mumbai"), ("USD-INR",)),
    ExposureRule("earthquake", ("china", "sichuan", "beijing"), ("USD-CNY", "^NKX")),
    ExposureRule("severestorms", ("united states", "atlantic", "gulf", "florida", "texas",
                                  "louisiana", "caribbean"), ("^SPX", "^DJI")),
    ExposureRule("severestorms", ("japan", "pacific", "philippines", "taiwan"), ("^NKX", "USD-JPY")),
    ExposureRule("wildfires", ("california", "canada", "australia", "mediterranean"), ("^SPX",)),
    ExposureRule("volcanoes", ("iceland", "indonesia", "japan", "italy"), ("^NKX", "USD-EUR")),
    ExposureRule("floods", ("india", "bangladesh", "pakistan", "mumbai"), ("USD-INR",)),
    ExposureRule("weather", ("new-york",), ("^SPX", "^NDQ", "^DJI")),
    ExposureRule("weather", ("london",), ("USD-GBP",)),
    ExposureRule("weather", ("tokyo",), ("^NKX", "USD-JPY")),
    ExposureRule("weather", ("mumbai",), ("USD-INR",)),
    ExposureRule("weather", ("frankfurt",), ("USD-EUR",)),
    ExposureRule("weather", ("singapore",), ("USD-CNY", "^NKX")),
    # Celestial (see docs/CELESTIAL_MARKET_WATCH.md): space weather has a
    # physical infrastructure channel; lunar/planetary/eclipse items are
    # behavioural correlation research only — causality never established.
    ExposureRule("geomagnetic", ("storm",), ("^SPX", "^NDQ", "^DJI")),
    ExposureRule("solar_wind", ("high-speed",), ("^SPX", "^NDQ")),
    ExposureRule("xray_flare", ("m-class", "x-class"), ("^SPX", "^NDQ")),
    ExposureRule("lunar", ("amavasya", "poornima"), ("^SPX", "^NDQ", "BTCUSDT")),
    ExposureRule("planetary", ("opposition", "close approach", "conjunction"),
                 ("^SPX", "^NDQ", "BTCUSDT")),
    ExposureRule("eclipse", ("eclipse",), ("^SPX", "^NDQ", "^DJI", "BTCUSDT")),
)


def _event_payload(event: Any) -> dict[str, Any]:
    """Accept an observation dict (contract), a payload JSON string or a
    payload dict; always return the payload dict. Fail-closed on junk."""
    candidate = event
    if isinstance(candidate, dict) and "payload" in candidate:
        candidate = candidate["payload"]
    if isinstance(candidate, str):
        try:
            candidate = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid_event_payload") from exc
    if not isinstance(candidate, dict):
        raise ValueError("invalid_event_payload")
    return candidate


def _event_text(payload: dict[str, Any]) -> str:
    parts = [
        str(payload.get("place", "")),
        str(payload.get("title", "")),
        str(payload.get("city", "")),
    ]
    parts.extend(str(item) for item in payload.get("categories", [])[:8])
    return " ".join(parts).lower()


def _event_summary(payload: dict[str, Any]) -> str:
    for key in ("place", "title", "city"):
        text = str(payload.get(key, "")).strip()
        if text:
            return text[:240]
    return "unlabelled_event"


def match_event_exposure(
    event: Any,
    *,
    rules: tuple[ExposureRule, ...] = DEFAULT_EXPOSURE_RULES,
) -> list[str]:
    """Instruments this event plausibly touches, per the exposure map.

    Returns an empty list when nothing matches — that is an honest
    "no known exposure", not an error.
    """
    payload = _event_payload(event)
    kind = str(payload.get("kind", "")).strip().lower()
    if not kind:
        raise ValueError("event_kind_missing")
    text = _event_text(payload)
    exposed: list[str] = []
    for rule in rules:
        if rule.kind != kind:
            continue
        if rule.keywords and not any(keyword in text for keyword in rule.keywords):
            continue
        for instrument in rule.instruments:
            if instrument not in exposed:
                exposed.append(instrument)
            if len(exposed) > MAX_EXPOSED_INSTRUMENTS:
                raise ValueError("too_many_exposed_instruments")
    return exposed


def assess_event_impact(
    event: Any,
    returns_map: dict[str, list[float]],
    *,
    rules: tuple[ExposureRule, ...] = DEFAULT_EXPOSURE_RULES,
    window: int = 20,
) -> dict[str, Any]:
    """Structured impact hypothesis for one observed world event.

    For every plausibly exposed instrument with enough return history,
    reports whether the latest move is statistically unusual (z-score vs
    its own baseline), its regime, and which sibling instruments led it.
    ``causality`` is always "not_established": an unusual move near an
    event is a research lead, never proof the event moved the market.
    """
    if not 2 <= window <= 200:
        raise ValueError("invalid_window")
    if not isinstance(returns_map, dict):
        raise ValueError("invalid_returns_map")
    payload = _event_payload(event)
    exposed = match_event_exposure(payload, rules=rules)

    assessments: list[dict[str, Any]] = []
    for instrument in exposed[:MAX_ASSESSMENTS]:
        series = returns_map.get(instrument)
        if series is None:
            assessments.append({"instrument": instrument, "status": "no_market_data"})
            continue
        if len(series) < window * 2:
            assessments.append({
                "instrument": instrument,
                "status": "insufficient_data",
                "points": len(series),
                "required": window * 2,
            })
            continue
        context = {
            other: returns_map[other]
            for other in exposed[:MAX_ASSESSMENTS]
            if other != instrument
            and isinstance(returns_map.get(other), list)
            and len(returns_map[other]) >= window * 2
        }
        try:
            move = explain_move(series, context, window=window)
        except ValueError:
            assessments.append({"instrument": instrument, "status": "insufficient_data",
                                "points": len(series), "required": window * 2})
            continue
        assessments.append({
            "instrument": instrument,
            "status": "assessed",
            "direction": move["direction"],
            "magnitude_z_score": move["magnitude_z_score"],
            "unusual_move": move["unusual_move"],
            "breadth_ratio": move["breadth_ratio"],
            "regime": move["regime"],
        })

    assessed = [item for item in assessments if item.get("status") == "assessed"]
    unusual = [item for item in assessed if item.get("unusual_move")]
    if not exposed:
        verdict = "no_known_exposure"
    elif unusual:
        verdict = "unusual_move_detected"
    elif assessed:
        verdict = "no_unusual_move"
    else:
        verdict = "insufficient_market_data"

    return {
        "event_kind": str(payload.get("kind", "")),
        "event_summary": _event_summary(payload),
        "event_time": payload.get("event_time"),
        "matched_instruments": exposed,
        "assessments": assessments,
        "verdict": verdict,
        "hypothesis": True,
        "causality": "not_established",
        "live_execution": False,
    }


def market_returns_from_store(
    store: ResearchStore,
    instruments: list[str] | tuple[str, ...],
    *,
    dataset: str = DATASET,
    points: int = 64,
) -> dict[str, list[float]]:
    """Rebuild per-instrument return series from ingested observations.

    Reads the immutable p2p_observations dataset newest-first, keeps rows
    whose record_key belongs to a requested instrument, orders the values
    oldest→newest by event_time and converts them to returns. Instruments
    with fewer than two usable quotes are omitted (never zero-filled).
    """
    if not 2 <= points <= MAX_RETURNS_POINTS:
        raise ValueError("invalid_points")
    wanted: list[str] = []
    for instrument in instruments:
        name = str(instrument).strip()
        if not name or len(name) > MAX_INSTRUMENT_LENGTH:
            raise ValueError("invalid_instrument")
        if name not in wanted:
            wanted.append(name)
    rows = store.list(dataset, limit=MAX_STORE_ROWS)

    quotes: dict[str, list[tuple[str, float]]] = {name: [] for name in wanted}
    for row in rows:
        record_key = str(row.get("record_key", ""))
        instrument, separator, _ = record_key.partition(":")
        if not separator or instrument not in quotes:
            continue
        stored = row.get("payload")
        if not isinstance(stored, dict):
            continue
        try:
            value = quote_value(stored)
        except ValueError:
            continue  # unparseable or valueless row; skip, never invent
        event_time = str(row.get("event_time", ""))
        quotes[instrument].append((event_time, value))

    series_map: dict[str, list[float]] = {}
    for instrument in wanted:
        observations = sorted(quotes[instrument], key=lambda item: item[0])[-points:]
        values = [value for _, value in observations]
        if len(values) < 2:
            continue
        try:
            series = returns_series(values)
        except ValueError:
            continue
        if series:
            series_map[instrument] = series
    return series_map
