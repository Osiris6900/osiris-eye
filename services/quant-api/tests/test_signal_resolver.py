"""Deterministic policy, persistence and RBAC evidence for signal arbitration."""

import base64
import json
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from osiris_api.control_plane.api import router
from osiris_api.control_plane.database import Database
from osiris_api.control_plane.identity import totp
from osiris_api.control_plane.signals import AUDITS, DECISIONS, SignalResolver


def signal(
    signal_id="signal-a",
    strategy="alpha",
    side="BUY",
    priority=10,
    event_ms=80,
    available_ms=90,
):
    return {
        "signal_id": signal_id,
        "strategy": strategy,
        "side": side,
        "priority": priority,
        "event_ms": event_ms,
        "available_ms": available_ms,
    }


def payload(signals=None, **changes):
    value = {
        "instrument": "BTC-USD",
        "as_of_ms": 100,
        "signals": [signal()] if signals is None else signals,
    }
    value.update(changes)
    return value


@pytest.fixture
def resolver(tmp_path):
    return SignalResolver(Database(str(tmp_path / "signals.db")))


def test_highest_priority_direction_is_order_independent_and_replayable(resolver):
    request = payload(
        [
            signal("low-sell", "contrarian", "SELL", 9),
            signal("buy-z", "zeta", "BUY", 10),
            signal("neutral", "risk-model", "HOLD", 1_000),
            signal("buy-a", "alpha", "BUY", 10),
        ]
    )
    first = resolver.resolve("decision-a", "alice", request)
    decision = first["decision"]
    assert first["replayed"] is False
    assert decision["elected_side"] == decision["resolved_side"] == "BUY"
    assert decision["highest_priority"] == 10
    assert decision["elected_signal_ids"] == ["buy-a", "buy-z"]
    assert decision["elected_strategies"] == ["alpha", "zeta"]
    assert decision["conflicting_signal_ids"] == []
    assert decision["resolution_reason"] == "highest_priority_consensus"
    assert decision["live_execution"] is decision["execution_authorized"] is False
    outcomes = {row["signal_id"]: row["outcome"] for row in first["audit"]["signal_outcomes"]}
    assert outcomes == {
        "buy-a": "elected",
        "buy-z": "elected",
        "low-sell": "lower_priority",
        "neutral": "neutral_hold",
    }

    replay = resolver.resolve(
        "decision-a", "alice", {**request, "signals": list(reversed(request["signals"]))}
    )
    assert replay == {"decision": decision, "audit": first["audit"], "replayed": True}
    assert len(resolver.db.list(DECISIONS)) == 1
    assert len(resolver.db.list(AUDITS)) == 1


def test_equal_priority_opposite_directions_fail_to_hold(resolver):
    result = resolver.resolve(
        "decision-tie",
        "alice",
        payload(
            [
                signal("sell-top", "beta", "SELL", 20),
                signal("buy-low", "gamma", "BUY", 1),
                signal("buy-top", "alpha", "BUY", 20),
            ]
        ),
    )["decision"]
    assert result["elected_side"] == result["resolved_side"] == "HOLD"
    assert result["resolution_reason"] == "equal_priority_direction_conflict"
    assert result["elected_signal_ids"] == []
    assert result["conflicting_signal_ids"] == ["buy-top", "sell-top"]
    assert result["highest_priority"] == 20


def test_risk_override_dominates_without_erasing_election_evidence(resolver):
    override = {
        "override_id": "risk-halt-1",
        "reason": "daily_loss_limit",
        "event_ms": 95,
        "available_ms": 99,
    }
    result = resolver.resolve("decision-risk", "risk-engine", payload(risk_override=override))
    decision = result["decision"]
    assert decision["elected_side"] == "BUY"
    assert decision["resolved_side"] == "HOLD"
    assert decision["resolution_reason"] == "risk_override"
    assert decision["risk_override_applied"] is True
    assert decision["risk_override_id"] == "risk-halt-1"
    assert decision["elected_signal_ids"] == ["signal-a"]
    assert result["audit"]["request"]["risk_override"] == override
    assert result["audit"]["actor"] == "risk-engine"


def test_unavailable_and_neutral_signals_cannot_create_a_trade(resolver):
    future = signal("future", "future-strategy", "BUY", 100, 100, 101)
    current = signal("current", "current-strategy", "SELL", 1)
    decision = resolver.resolve("decision-time", "alice", payload([future, current]))["decision"]
    assert decision["excluded_signal_ids"] == ["future"]
    assert decision["eligible_signal_ids"] == ["current"]
    assert decision["resolved_side"] == "SELL"

    neutral = resolver.resolve("decision-neutral", "alice", payload([future, signal(side="HOLD")]))[
        "decision"
    ]
    assert neutral["resolved_side"] == "HOLD"
    assert neutral["resolution_reason"] == "no_actionable_signal"
    assert neutral["highest_priority"] is None

    empty = resolver.resolve("decision-empty", "alice", payload([]))["decision"]
    assert empty["resolved_side"] == "HOLD"


@pytest.mark.parametrize(
    "candidate,error",
    [
        (payload([signal(), signal()]), "invalid_or_duplicate_signal"),
        (payload([signal(side="buy")]), "invalid_or_duplicate_signal"),
        (payload([signal(priority=True)]), "invalid_or_duplicate_signal"),
        (payload([signal(priority=-1)]), "invalid_or_duplicate_signal"),
        (payload([signal(event_ms=91, available_ms=90)]), "invalid_or_duplicate_signal"),
        (payload([signal(available_ms=True)]), "invalid_signal_available_ms"),
        (payload(as_of_ms=-1), "invalid_as_of_ms"),
        (payload(instrument="bad instrument"), "invalid_identifier"),
        (payload([signal()], unexpected=True), "invalid_signal_resolution_request"),
        (payload([{**signal(), "quantity": "1"}]), "invalid_signal_fields"),
        (payload([signal()] * 101), "invalid_signal_count"),
        (
            payload(
                risk_override={
                    "override_id": "late-risk",
                    "reason": "late",
                    "event_ms": 100,
                    "available_ms": 101,
                }
            ),
            "risk_override_not_available_at_as_of",
        ),
        (
            payload(risk_override={"override_id": "risk", "reason": "halt"}),
            "invalid_risk_override",
        ),
    ],
)
def test_invalid_inputs_fail_closed_without_partial_records(resolver, candidate, error):
    with pytest.raises(ValueError, match=error):
        resolver.resolve("invalid-decision", "alice", candidate)
    assert resolver.db.list(DECISIONS) == []
    assert resolver.db.list(AUDITS) == []


def test_id_conflict_cannot_change_inputs_actor_or_original_decision(resolver):
    original = resolver.resolve("decision-a", "alice", payload())["decision"]
    with pytest.raises(ValueError, match="signal_decision_id_conflict"):
        resolver.resolve("decision-a", "alice", payload([signal(side="SELL")]))
    with pytest.raises(ValueError, match="signal_decision_id_conflict"):
        resolver.resolve("decision-a", "bob", payload())
    assert resolver.get("decision-a") == original


def test_new_database_object_returns_verified_decision_and_audit(resolver):
    created = resolver.resolve("decision-a", "alice", payload())
    restarted = SignalResolver(Database(resolver.db.path))
    assert restarted.get("decision-a") == created["decision"]
    assert restarted.audit("decision-a") == created["audit"]
    assert restarted.get("missing") is None
    assert restarted.audit("missing") is None


def test_decision_or_audit_tampering_is_rejected(resolver):
    created = resolver.resolve("decision-a", "alice", payload())
    resolver.db.put(
        DECISIONS,
        "decision-a",
        {**created["decision"], "resolved_side": "SELL"},
        expected_version=1,
    )
    with pytest.raises(ValueError, match="signal_resolution_audit_mismatch"):
        resolver.get("decision-a")

    other = resolver.resolve("decision-b", "alice", payload())
    with resolver.db.transaction() as conn:
        conn.execute(
            "DELETE FROM cp_documents WHERE collection=? AND id=?",
            (AUDITS, "decision-b"),
        )
    with pytest.raises(ValueError, match="signal_resolution_audit_incomplete"):
        resolver.audit("decision-b")
    assert other["decision"]["resolved_side"] == "BUY"


def test_concurrent_identical_retries_create_one_decision(resolver):
    request = payload()

    def resolve(_):
        return resolver.resolve("decision-concurrent", "alice", request)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(resolve, range(8)))
    assert sum(not result["replayed"] for result in results) == 1
    assert all(result["decision"] == results[0]["decision"] for result in results)
    assert len(resolver.db.list(DECISIONS)) == 1
    assert len(resolver.db.list(AUDITS)) == 1


def test_concurrent_conflicting_ids_have_one_winner(resolver):
    def resolve(side):
        try:
            return resolver.resolve("decision-race", "alice", payload([signal(side=side)]))[
                "decision"
            ]["resolved_side"]
        except ValueError as exc:
            assert str(exc) == "signal_decision_id_conflict"
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(resolve, ["BUY", "SELL"]))
    assert sorted(results) in (["BUY", "conflict"], ["SELL", "conflict"])
    assert resolver.get("decision-race")["resolved_side"] in {"BUY", "SELL"}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_CONTROL_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("OSIRIS_CONTROL_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv(
        "OSIRIS_ADMIN_TOKENS",
        json.dumps({"analyst": "fixture-analyst-token", "viewer": "fixture-viewer-token"}),
    )
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


def analyst_session(api):
    header = {"Authorization": "Bearer fixture-analyst-token"}
    enrollment = api.post("/control-plane/auth/enroll", headers=header)
    assert enrollment.status_code == 200
    login = api.post(
        "/control-plane/auth/login",
        headers=header,
        json={"code": totp(enrollment.json()["secret"], int(time.time() // 30))},
    )
    assert login.status_code == 200
    return {"Authorization": "Session " + login.json()["session_token"]}


def test_api_requires_mfa_for_resolution_and_research_scope_for_reads(api):
    path = "/control-plane/signals/resolve"
    envelope = {"id": "decision-api", "payload": payload()}
    analyst = {"Authorization": "Bearer fixture-analyst-token"}
    viewer = {"Authorization": "Bearer fixture-viewer-token"}
    assert api.post(path, json=envelope).status_code == 401
    assert api.post(path, json=envelope, headers=analyst).status_code == 403
    session = analyst_session(api)
    created = api.post(path, json=envelope, headers=session)
    assert created.status_code == 200, created.text
    decision = created.json()["decision"]
    assert decision["resolved_side"] == "BUY"
    assert decision["live_execution"] is decision["execution_authorized"] is False

    decision_path = "/control-plane/signals/decision-api"
    audit_path = decision_path + "/audit"
    assert api.get(decision_path).status_code == 401
    assert api.get(decision_path, headers=viewer).status_code == 403
    exported = api.get(decision_path, headers=analyst)
    assert exported.status_code == 200
    assert exported.json() == decision
    audited = api.get(audit_path, headers=analyst)
    assert audited.status_code == 200
    assert audited.json() == created.json()["audit"]
    assert api.get("/control-plane/signals/missing", headers=analyst).status_code == 404
    assert api.get("/control-plane/signals/missing/audit", headers=analyst).status_code == 404

    replay = api.post(path, json=envelope, headers=session)
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    conflict = {"id": "decision-api", "payload": payload([signal(side="SELL")])}
    assert api.post(path, json=conflict, headers=session).status_code == 422
    assert api.post(path, json={**envelope, "actor": "forged"}, headers=session).status_code == 422
    assert "fixture-analyst-token" not in exported.text + audited.text


def test_api_tamper_failure_does_not_return_unverified_decision(api):
    session = analyst_session(api)
    created = api.post(
        "/control-plane/signals/resolve",
        json={"id": "decision-api", "payload": payload()},
        headers=session,
    )
    assert created.status_code == 200
    connection = sqlite3.connect(os.environ["OSIRIS_CONTROL_DB"])
    try:
        connection.execute(
            "UPDATE cp_documents SET body=? WHERE collection=? AND id=?",
            ("{}", AUDITS, "decision-api"),
        )
        connection.commit()
    finally:
        connection.close()
    response = api.get(
        "/control-plane/signals/decision-api",
        headers={"Authorization": "Bearer fixture-analyst-token"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "signal_resolution_audit_invalid"
