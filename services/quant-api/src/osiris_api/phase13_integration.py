"""Phase 13 end-to-end point-in-time data to portfolio-risk gate.

This composes the local observation store with deterministic freshness and risk
checks. It is a research/paper control only; it never authorizes live execution.
"""
from __future__ import annotations

from datetime import timezone
from typing import Any

from .phase4_systems import parse_utc
from .phase8_systems import portfolio_risk_governor
from .phase11_systems import evaluate_kill_switch


def evaluate_data_to_risk_pipeline(
    observations: list[dict[str, Any]],
    as_of: str,
    max_age_seconds: float,
    positions: list[dict[str, Any]],
    max_gross_exposure: float,
    max_single_name_weight: float,
    current_drawdown: float,
    max_drawdown: float,
    manual_halt: bool = False,
) -> dict[str, Any]:
    if not isinstance(observations, list):
        raise ValueError("observations must be a list")
    if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float)):
        raise ValueError("max_age_seconds must be a finite non-negative number")
    if not 0 <= max_age_seconds < float("inf"):
        raise ValueError("max_age_seconds must be a finite non-negative number")
    cutoff = parse_utc(as_of)
    if not observations:
        feed = {"status": "missing", "records": 0, "new_risk_allowed": False,
                "latest_event_time": None, "latest_available_at": None}
    else:
        parsed = []
        for row in observations:
            if not isinstance(row, dict):
                raise ValueError("each observation must be an object")
            event_time = parse_utc(row.get("event_time"))
            available_at = parse_utc(row.get("available_at"))
            if event_time > cutoff or available_at > cutoff:
                raise ValueError("point-in-time query returned a future observation")
            parsed.append((event_time, available_at))
        latest_event = max(x[0] for x in parsed)
        latest_available = max(x[1] for x in parsed)
        event_age = (cutoff - latest_event).total_seconds()
        availability_age = (cutoff - latest_available).total_seconds()
        stale = event_age > max_age_seconds or availability_age > max_age_seconds
        feed = {
            "status": "stale" if stale else "fresh",
            "records": len(observations),
            "event_age_seconds": event_age,
            "availability_age_seconds": availability_age,
            "latest_event_time": latest_event.isoformat(),
            "latest_available_at": latest_available.isoformat(),
            "new_risk_allowed": not stale,
        }
    portfolio = portfolio_risk_governor(
        positions, max_gross_exposure, max_single_name_weight,
        current_drawdown, max_drawdown,
    )
    kill = evaluate_kill_switch(
        {"point_in_time_data": feed["new_risk_allowed"],
         "portfolio_risk": portfolio["new_risk_allowed"]},
        len(portfolio["breaches"]), not feed["new_risk_allowed"], manual_halt,
    )
    allowed = feed["new_risk_allowed"] and portfolio["new_risk_allowed"] and kill["action"] == "MONITOR"
    return {
        "status": "within_limits" if allowed else "blocked",
        "point_in_time": {"as_of": cutoff.isoformat(), **feed},
        "portfolio_risk": portfolio,
        "risk_gate": kill,
        "new_risk_allowed": allowed,
        "live_execution_enabled": False,
        "limitations": [
            "Feed freshness is based on stored event_time and available_at, not independent vendor validation",
            "Portfolio exposures and drawdown are caller-supplied and must be independently reconciled",
            "This endpoint does not submit, modify, or cancel orders",
        ],
    }
