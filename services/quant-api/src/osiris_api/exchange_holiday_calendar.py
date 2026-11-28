"""Sourced, versioned holiday closes for instrument-master exchange venues.

A date inside a venue's seeded coverage is closed only when a cited source
was already published. A date outside that coverage fails closed. This is not
a live circular feed. Early closes are not full-day holidays.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any


def _load() -> dict[str, Any]:
    path = Path(__file__).with_name("exchange_holiday_calendar.json")
    raw = path.read_bytes()
    payload = json.loads(raw)
    if payload.get("schema") != "osiris.exchange-holiday-calendar.v1":
        raise ValueError("invalid_exchange_holiday_calendar")
    sources = payload.get("sources")
    venues = payload.get("venues")
    if not isinstance(sources, dict) or not isinstance(venues, dict) or not venues:
        raise ValueError("invalid_exchange_holiday_calendar")
    compiled = {}
    for venue, body in venues.items():
        if not isinstance(venue, str) or not isinstance(body, dict):
            raise ValueError("invalid_exchange_holiday_calendar")
        start = date.fromisoformat(body["coverage_start"])
        end = date.fromisoformat(body["coverage_end"])
        if end < start:
            raise ValueError("invalid_exchange_holiday_calendar")
        holidays = []
        seen = set()
        for row in body.get("holidays", []):
            holiday = date.fromisoformat(row["date"])
            source_id = row["source"]
            source = sources.get(source_id)
            if not isinstance(source, dict) or not source.get("url") or not source.get("published"):
                raise ValueError("invalid_exchange_holiday_calendar")
            if not start <= holiday <= end or holiday in seen:
                raise ValueError("invalid_exchange_holiday_calendar")
            seen.add(holiday)
            holidays.append({
                "date": holiday,
                "name": row["name"],
                "source": source_id,
                "effective_at": datetime.fromisoformat(source["published"]).replace(tzinfo=timezone.utc),
            })
        compiled[venue] = {"start": start, "end": end, "holidays": holidays}
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "sources": sources,
        "venues": compiled,
        "venue_names": sorted(compiled),
    }


@lru_cache(maxsize=1)
def calendar() -> dict[str, Any]:
    return _load()


def summary() -> dict[str, Any]:
    """Public description of the seeded coverage. Not a broker calendar."""
    loaded = calendar()
    venues = []
    for name in loaded["venue_names"]:
        body = loaded["venues"][name]
        venues.append({
            "venue": name,
            "coverage_start": body["start"].isoformat(),
            "coverage_end": body["end"].isoformat(),
            "holiday_count": len(body["holidays"]),
        })
    return {
        "schema": "osiris.exchange-holiday-calendar.v1",
        "sha256": loaded["sha256"],
        "venues": venues,
        "sources": [
            {"id": key, "title": value.get("title"), "published": value.get("published"), "url": value["url"]}
            for key, value in sorted(loaded["sources"].items())
        ],
        "early_closes_seeded": False,
        "bse_seeded": False,
        "live_calendar_feed": False,
    }


def session_holiday_reason(venue: object, local_day: date, as_of: datetime) -> str | None:
    """Return a refusal reason, or None when the local date is a covered weekday.

    ``as_of`` is the decision time. A holiday published after that time is not
    yet in force.
    """
    if not isinstance(venue, str) or not venue.strip():
        return "exchange_calendar_unavailable"
    loaded = calendar()
    body = loaded["venues"].get(venue.strip().upper())
    if body is None:
        return "exchange_calendar_unavailable"
    if local_day < body["start"] or local_day > body["end"]:
        return "exchange_calendar_out_of_range"
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        return "exchange_calendar_unavailable"
    known = as_of.astimezone(timezone.utc)
    for holiday in body["holidays"]:
        if holiday["date"] == local_day and holiday["effective_at"] <= known:
            return "exchange_holiday"
    return None
