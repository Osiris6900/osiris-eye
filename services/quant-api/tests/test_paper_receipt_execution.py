"""A genuine kernel-authenticated fixture receipt never bypasses current risk."""

import hashlib
import json
import os
import socket
import struct
from datetime import UTC, datetime, timedelta

import pytest

from osiris_api import paper_execution as execution
from osiris_api.control_plane.database import digest
from osiris_api.data_store import ResearchStore
from osiris_api.paper_receipt_execution import execute_receipted_observation
from osiris_api.receipt_gated_retry import AdmissionDecision, ReceiptRetryScheduler
from osiris_api.recipient_receipt_ledger import RecipientReceiptLedger
from osiris_api.verified_receipt_consumer import VerifiedReceiptConsumer
from osiris_api.verified_receipt_event import VERIFIED_EVENT_PROVENANCE, VERIFIED_EVENT_SCHEMA
from osiris_api.verified_receipt_unix import receive_verified_receipt

NOW = datetime(2026, 11, 12, 19, 0, tzinfo=UTC)


def acknowledge(consumer, event):
    left, right = socket.socketpair()
    try:
        raw = json.dumps(event).encode()
        left.sendall(struct.pack("!I", len(raw)) + raw)
        return receive_verified_receipt(
            right,
            consumer,
            expected_rust_pid=os.getpid(),
            expected_rust_uid=os.getuid(),
            now=int(NOW.timestamp()),
        )
    finally:
        left.close()
        right.close()


@pytest.fixture
def flow(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "execution.db"))
    monkeypatch.setenv("OSIRIS_P2P_INGEST_ENABLED", "1")
    monkeypatch.setenv("OSIRIS_RECEIPT_PAPER_ENABLED", "1")
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    execution.initialize_paper_account("1000")
    ledger = RecipientReceiptLedger(str(tmp_path / "receipts.db"))
    consumer = VerifiedReceiptConsumer(ledger, sender_peer_id="sender")
    scheduler = ReceiptRetryScheduler(str(tmp_path / "retry.db"), ledger, enabled=True)
    raw = json.dumps({"last": "100", "source_mode": "synthetic"})
    observation = {
        "schema": "osiris.observation.v1",
        "instrument": "TEST",
        "provider": "synthetic-fixture",
        "observed_at": NOW.isoformat(),
        "payload": raw,
        "payload_sha256": hashlib.sha256(raw.encode()).hexdigest(),
    }
    key = ":".join((observation["provider"], "TEST", observation["payload_sha256"]))
    policy = AdmissionDecision({"recipient"}, set())
    assert scheduler.schedule(observation, "recipient", now=int(NOW.timestamp()))
    scheduler.advance(
        publish=lambda *_: {
            "status": "tracked",
            "message_id": "message",
            "envelope_sha256": "a" * 64,
        },
        allowed_providers={"synthetic-fixture"},
        policy=policy,
        now=int(NOW.timestamp()),
    )
    event = {
        "schema": VERIFIED_EVENT_SCHEMA,
        "provenance": VERIFIED_EVENT_PROVENANCE,
        "event_id": "event",
        "sender_peer_id": "sender",
        "recipient_peer_id": "recipient",
        "message_id": "message",
        "envelope_sha256": "a" * 64,
        "accepted_at": int(NOW.timestamp()),
    }
    args = {
        "observation_key": key,
        "recipient": "recipient",
        "policy": policy,
        "allowed_providers": {"synthetic-fixture"},
        "strategy_id": "fixture-v1",
        "signals": [0, 1],
        "asset_returns": [0, 0.01],
        "quantity": "1",
        "fee": "1",
        "risk_service_available": True,
        "now": NOW,
        "feed_policy": {
            "synthetic-fixture": {
                "authorized_source": "disposable-fixture-only",
                "entitlement_reference": "synthetic-only",
                "redistribution_rights": "permitted",
                "availability_timestamps_required": True,
                "instruments": ["TEST"],
                "quality_review": {
                    "status": "passed",
                    "reviewer": "fixture",
                    "reviewed_at": NOW.isoformat(),
                },
            }
        },
    }
    store = ResearchStore(str(tmp_path / "research.db"))
    return scheduler, consumer, store, event, args


def ready(flow):
    scheduler, consumer, _store, event, args = flow
    assert acknowledge(consumer, event) == "applied"
    scheduler.advance(
        publish=lambda *_: pytest.fail("republished acknowledged observation"),
        allowed_providers=args["allowed_providers"],
        policy=args["policy"],
        now=int(NOW.timestamp()),
    )


def run(flow, **changes):
    scheduler, consumer, store, _event, args = flow
    return execute_receipted_observation(scheduler, consumer, store, **{**args, **changes})


def test_receipt_is_required_before_any_order(flow):
    with pytest.raises(ValueError, match="verified_receipt_required"):
        run(flow)
    assert execution.snapshot()["fill_count"] == 0


def test_receipt_to_risk_fill_reconciliation_and_restart(flow):
    ready(flow)
    first = run(flow)
    assert first["order"]["status"] == "filled"
    assert first["receipt"]["sender_peer_id"] == "sender"
    assert first["receipt"]["rust_pid"] == os.getpid()
    assert first["account"]["cash"] == "899"
    assert first["account"]["unrealized_pnl"] == "-1"
    assert first["reconciliation"]["passed"]
    assert first["receipt_is_order_approval"] is False
    assert first["source_mode"] == "operator-declared"  # A peer's mode claim is not authority.
    assert first["live_execution"] is False
    assert first["broker_order_submitted"] is False
    scheduler, consumer, store, _event, args = flow
    reopened = VerifiedReceiptConsumer(
        RecipientReceiptLedger(consumer.ledger.path), sender_peer_id="sender"
    )
    replay = execute_receipted_observation(
        scheduler, reopened, ResearchStore(store.database_path), **args
    )
    assert replay["replayed"] is True
    assert replay["order"]["risk_decision_id"] == first["order"]["risk_decision_id"]
    assert execution.snapshot()["fill_count"] == 1


@pytest.mark.parametrize("control", ["kill", "risk-down", "revoked", "rights", "stale", "future"])
def test_current_controls_cannot_be_bypassed_by_acknowledged_receipt(flow, control):
    ready(flow)
    changes = {}
    if control == "kill":
        execution.set_kill_switch(True)
    elif control == "risk-down":
        changes["risk_service_available"] = False
    elif control == "revoked":
        changes["policy"] = AdmissionDecision({"recipient"}, {"recipient"})
    elif control == "rights":
        changes["feed_policy"] = {}
    elif control == "stale":
        changes["now"] = NOW + timedelta(seconds=301)
    elif control == "future":
        changes["now"] = NOW - timedelta(seconds=1)
    if control in {"kill", "risk-down"}:
        result = run(flow, **changes)
        assert result["order"]["status"] == "not_submitted"
        assert result["workflow"]["risk"]["approved_for_paper"] is False
    else:
        with pytest.raises(ValueError):
            run(flow, **changes)
    assert execution.snapshot()["fill_count"] == 0


def test_replay_shows_current_closed_risk_without_another_fill(flow):
    ready(flow)
    first = run(flow)
    execution.set_kill_switch(True)
    again = run(flow)
    assert again["trace_id"] == first["trace_id"]
    assert again["current_risk"]["approved_for_paper"] is False
    assert again["new_execution_authorized"] is False
    assert execution.snapshot()["fill_count"] == 1


@pytest.mark.parametrize("changes", [{"quantity": "2"}, {"fee": "2"}, {"signals": [0, -1]}])
def test_conflicting_economics_cannot_reuse_the_observation(flow, changes):
    ready(flow)
    run(flow)
    with pytest.raises(ValueError, match="pipeline_decision_conflict"):
        run(flow, **changes)
    assert execution.snapshot()["fill_count"] == 1


def test_unbound_legacy_receipt_is_not_a_paper_execution_proof(flow):
    ready(flow)
    with flow[1].ledger._connect() as db:
        db.execute("DELETE FROM verified_receipt_origins")
    with pytest.raises(ValueError, match="verified_receipt_provenance_required"):
        run(flow)
    assert execution.snapshot()["fill_count"] == 0


def test_disabled_path_never_reads_or_creates_state(monkeypatch):
    monkeypatch.delenv("OSIRIS_RECEIPT_PAPER_ENABLED", raising=False)
    with pytest.raises(ValueError, match="receipt_paper_execution_disabled"):
        execute_receipted_observation(
            None,
            None,
            None,
            observation_key="",
            recipient="",
            allowed_providers=set(),
            policy=None,
            feed_policy={},
            strategy_id="",
            signals=[],
            asset_returns=[],
            quantity="1",
        )


def test_an_unrelated_existing_fill_cannot_be_relabelled_as_receipt_driven(flow):
    ready(flow)
    args = flow[4]
    key = "receipt-paper-" + digest(
        {"strategy": args["strategy_id"], "observation_key": args["observation_key"]}
    )
    execution.submit_paper_order(
        client_order_id=key,
        symbol="TEST",
        side="BUY",
        quantity="1",
        price="100",
        fee="1",
        observed_at=NOW.isoformat(),
        now=NOW.isoformat(),
    )
    with pytest.raises(ValueError, match="receipt_paper_origin_unbound"):
        run(flow)
    assert execution.snapshot()["fill_count"] == 1


def test_origin_is_bound_to_canonical_audit_and_cannot_change_on_replay(flow):
    ready(flow)
    result = run(flow)
    original = execution.get_paper_order(result["trace_id"])
    assert len(original["origin_reference"]) == 64
    assert execution.recover()["audit_ok"]
    with pytest.raises(ValueError, match="idempotency_key_conflict"):
        execution.submit_paper_order(
            client_order_id=result["trace_id"],
            symbol="TEST",
            side="BUY",
            quantity="1",
            price="100",
            fee="1",
            origin_reference="b" * 64,
            observed_at=NOW.isoformat(),
            now=NOW.isoformat(),
        )
    assert (
        execution.get_paper_order(result["trace_id"])["origin_reference"]
        == original["origin_reference"]
    )
    assert execution.snapshot()["fill_count"] == 1


def test_http_order_submission_cannot_set_internal_origin_reference(flow):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(execution.router)
    response = TestClient(app).post(
        "/paper/execution/orders",
        json={
            "client_order_id": "http-origin",
            "symbol": "TEST",
            "side": "BUY",
            "quantity": "1",
            "price": "100",
            "origin_reference": "a" * 64,
            "observed_at": NOW.isoformat(),
            "now": NOW.isoformat(),
        },
    )
    assert response.status_code == 200
    assert "origin_reference" not in execution.get_paper_order("http-origin")


@pytest.mark.parametrize(
    "changes",
    [
        {"allowed_providers": set()},
        {"feed_policy": {}},
        {"source_mode": "live"},
        {"quantity": "1.0000000000000000000001"},
        {"quantity": "1e-999999999"},
        {"risk_service_available": "true"},
    ],
)
def test_invalid_or_unapproved_inputs_do_not_create_a_fill(flow, changes):
    ready(flow)
    with pytest.raises(ValueError):
        run(flow, **changes)
    assert execution.snapshot()["fill_count"] == 0


def test_changed_rights_reference_is_not_silently_substituted_on_replay(flow):
    ready(flow)
    run(flow)
    policy = json.loads(json.dumps(flow[4]["feed_policy"]))
    policy["synthetic-fixture"]["entitlement_reference"] = "another-contract"
    with pytest.raises(ValueError, match="receipt_feed_policy_changed"):
        run(flow, feed_policy=policy)
    assert execution.snapshot()["fill_count"] == 1
