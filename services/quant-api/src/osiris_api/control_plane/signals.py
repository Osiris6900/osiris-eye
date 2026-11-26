"""Deterministic, persistent research-signal arbitration; never routes orders."""

from __future__ import annotations

import json

from .database import MAX_SQLITE_INTEGER, Database, canonical, digest, identifier

DECISIONS = "signal-decisions"
AUDITS = "signal-resolution-audits"
POLICY = "highest-priority-direction-with-risk-hold-v1"
SIDES = {"BUY", "SELL", "HOLD"}


def _milliseconds(value: object, name: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SQLITE_INTEGER:
        raise ValueError(f"invalid_{name}")
    return value


def _reason(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 200
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("invalid_risk_override_reason")
    return value


def _normalize(payload: object) -> dict:
    if not isinstance(payload, dict) or set(payload) - {
        "instrument",
        "as_of_ms",
        "signals",
        "risk_override",
    }:
        raise ValueError("invalid_signal_resolution_request")
    if not {"instrument", "as_of_ms", "signals"} <= set(payload):
        raise ValueError("invalid_signal_resolution_request")
    instrument = identifier(payload["instrument"])
    as_of_ms = _milliseconds(payload["as_of_ms"], "as_of_ms")
    signals = payload["signals"]
    if not isinstance(signals, list) or len(signals) > 100:
        raise ValueError("invalid_signal_count")
    normalized = []
    seen = set()
    for signal in signals:
        if not isinstance(signal, dict) or set(signal) != {
            "signal_id",
            "strategy",
            "side",
            "priority",
            "event_ms",
            "available_ms",
        }:
            raise ValueError("invalid_signal_fields")
        signal_id = identifier(signal["signal_id"])
        strategy = identifier(signal["strategy"])
        side = signal["side"]
        priority = signal["priority"]
        event_ms = _milliseconds(signal["event_ms"], "signal_event_ms")
        available_ms = _milliseconds(signal["available_ms"], "signal_available_ms")
        if (
            signal_id in seen
            or side not in SIDES
            or type(priority) is not int
            or not 0 <= priority <= 1_000_000
            or event_ms > available_ms
        ):
            raise ValueError("invalid_or_duplicate_signal")
        seen.add(signal_id)
        normalized.append(
            {
                "signal_id": signal_id,
                "strategy": strategy,
                "side": side,
                "priority": priority,
                "event_ms": event_ms,
                "available_ms": available_ms,
            }
        )
    normalized.sort(key=lambda row: (row["strategy"], row["signal_id"]))
    override = payload.get("risk_override")
    if override is not None:
        if not isinstance(override, dict) or set(override) != {
            "override_id",
            "reason",
            "event_ms",
            "available_ms",
        }:
            raise ValueError("invalid_risk_override")
        override = {
            "override_id": identifier(override["override_id"]),
            "reason": _reason(override["reason"]),
            "event_ms": _milliseconds(override["event_ms"], "risk_event_ms"),
            "available_ms": _milliseconds(override["available_ms"], "risk_available_ms"),
        }
        if override["event_ms"] > override["available_ms"] or override["available_ms"] > as_of_ms:
            # Never silently ignore a supplied risk control.
            raise ValueError("risk_override_not_available_at_as_of")
    return {
        "instrument": instrument,
        "as_of_ms": as_of_ms,
        "signals": normalized,
        "risk_override": override,
    }


def _build(decision_id: str, payload: object, actor: str) -> tuple[dict, dict]:
    decision_id, actor = identifier(decision_id), identifier(actor)
    request = _normalize(payload)
    input_sha256 = digest(
        {
            "decision_id": decision_id,
            "actor": actor,
            "policy": POLICY,
            "request": request,
        }
    )
    as_of_ms = request["as_of_ms"]
    eligible = [signal for signal in request["signals"] if signal["available_ms"] <= as_of_ms]
    actionable = [signal for signal in eligible if signal["side"] != "HOLD"]
    highest_priority = max((signal["priority"] for signal in actionable), default=None)
    leaders = [signal for signal in actionable if signal["priority"] == highest_priority]
    leader_sides = {signal["side"] for signal in leaders}
    conflicted = len(leader_sides) > 1
    elected = (
        [] if conflicted else sorted(leaders, key=lambda row: (row["strategy"], row["signal_id"]))
    )
    elected_side = "HOLD" if not actionable or conflicted else next(iter(leader_sides))
    if not actionable:
        resolution_reason = "no_actionable_signal"
    elif conflicted:
        resolution_reason = "equal_priority_direction_conflict"
    else:
        resolution_reason = "highest_priority_consensus"

    outcomes = []
    elected_ids = {signal["signal_id"] for signal in elected}
    leader_ids = {signal["signal_id"] for signal in leaders}
    for signal in sorted(request["signals"], key=lambda row: row["signal_id"]):
        signal_id = signal["signal_id"]
        if signal["available_ms"] > as_of_ms:
            outcome = "not_available_at_as_of"
        elif signal_id in elected_ids:
            outcome = "elected"
        elif conflicted and signal_id in leader_ids:
            outcome = "equal_priority_direction_conflict"
        elif signal["side"] == "HOLD":
            outcome = "neutral_hold"
        else:
            outcome = "lower_priority"
        outcomes.append({"signal_id": signal_id, "outcome": outcome})

    override = request["risk_override"]
    risk_applied = override is not None
    resolved_side = "HOLD" if risk_applied else elected_side
    if risk_applied:
        resolution_reason = "risk_override"
    core = {
        "schema": "osiris.signal-resolution.v1",
        "decision_id": decision_id,
        "instrument": request["instrument"],
        "as_of_ms": as_of_ms,
        "input_sha256": input_sha256,
        "policy": POLICY,
        "eligible_signal_ids": sorted(signal["signal_id"] for signal in eligible),
        "excluded_signal_ids": sorted(
            signal["signal_id"]
            for signal in request["signals"]
            if signal["available_ms"] > as_of_ms
        ),
        "highest_priority": highest_priority,
        "elected_side": elected_side,
        "elected_signal_ids": sorted(elected_ids),
        "elected_strategies": sorted({signal["strategy"] for signal in elected}),
        "conflicting_signal_ids": sorted(leader_ids) if conflicted else [],
        "resolved_side": resolved_side,
        "resolution_reason": resolution_reason,
        "risk_override_applied": risk_applied,
        "risk_override_id": override["override_id"] if override else None,
        "live_execution": False,
        "execution_authorized": False,
    }
    decision_sha256 = digest(core)
    audit = {
        "schema": "osiris.signal-resolution-audit.v1",
        "decision_id": decision_id,
        "event": "signal_resolved",
        "actor": actor,
        "policy": POLICY,
        "input_sha256": input_sha256,
        "decision_sha256": decision_sha256,
        "request": request,
        "signal_outcomes": outcomes,
        "elected_side": elected_side,
        "resolved_side": resolved_side,
        "resolution_reason": resolution_reason,
        "risk_override_applied": risk_applied,
        "live_execution": False,
        "execution_authorized": False,
    }
    decision = {
        **core,
        "decision_sha256": decision_sha256,
        "audit_sha256": digest(audit),
    }
    return decision, audit


class SignalResolver:
    """Arbitrate research signals and persist one immutable decision plus its audit."""

    def __init__(self, db: Database):
        self.db = db

    def _stored(self, conn, decision_id: str) -> tuple[dict, dict] | None:
        rows = conn.execute(
            "SELECT collection,body FROM cp_documents WHERE collection IN (?,?) AND id=?",
            (DECISIONS, AUDITS, decision_id),
        ).fetchall()
        if not rows:
            return None
        if len(rows) != 2 or {row["collection"] for row in rows} != {DECISIONS, AUDITS}:
            raise ValueError("signal_resolution_audit_incomplete")
        values = {row["collection"]: json.loads(row["body"]) for row in rows}
        decision, audit = values[DECISIONS], values[AUDITS]
        actor = audit.get("actor") if isinstance(audit, dict) else None
        request = audit.get("request") if isinstance(audit, dict) else None
        try:
            expected_decision, expected_audit = _build(decision_id, request, actor)
        except (ValueError, TypeError, AttributeError):
            raise ValueError("signal_resolution_audit_invalid") from None
        if decision != expected_decision or audit != expected_audit:
            raise ValueError("signal_resolution_audit_mismatch")
        return decision, audit

    def resolve(self, decision_id: str, actor: str, payload: object) -> dict:
        decision, audit = _build(decision_id, payload, actor)
        decision_id = decision["decision_id"]
        with self.db.transaction() as conn:
            stored = self._stored(conn, decision_id)
            if stored is not None:
                old_decision, old_audit = stored
                if old_decision["input_sha256"] != decision["input_sha256"]:
                    raise ValueError("signal_decision_id_conflict")
                return {
                    "decision": old_decision,
                    "audit": old_audit,
                    "replayed": True,
                }
            conn.execute(
                "INSERT INTO cp_documents VALUES (?,?,?,?)",
                (DECISIONS, decision_id, 1, canonical(decision).decode()),
            )
            conn.execute(
                "INSERT INTO cp_documents VALUES (?,?,?,?)",
                (AUDITS, decision_id, 1, canonical(audit).decode()),
            )
        return {"decision": decision, "audit": audit, "replayed": False}

    def get(self, decision_id: str) -> dict | None:
        with self.db.transaction() as conn:
            stored = self._stored(conn, identifier(decision_id))
        return None if stored is None else stored[0]

    def audit(self, decision_id: str) -> dict | None:
        with self.db.transaction() as conn:
            stored = self._stored(conn, identifier(decision_id))
        return None if stored is None else stored[1]
