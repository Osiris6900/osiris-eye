"""Package 42: paper order and fill events need a reliable local bus.

On main, before this module existed, consumers of paper order, fill, mark,
risk and reconciliation events had no per-stream sequence, no ack offset, and
no replay. The tests below are the contract those gaps failed.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app
from osiris_api.paper_event_bus import (
    PaperEventBus,
    dedupe_events,
    detect_gaps,
)
from osiris_api.paper_execution import (
    LIVE_EXECUTION_ENABLED,
    apply_paper_fill,
    cancel_paper_order,
    initialize_paper_account,
    mark_to_market,
    reconciliation_report,
    set_kill_switch,
    submit_paper_order,
)
from osiris_api.risk import RiskLimits

NOW = "2026-11-08T16:00:00+00:00"
FRESH = "2026-11-08T15:59:30+00:00"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "paper-event-bus.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(path))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    monkeypatch.delenv("OSIRIS_API_TOKEN", raising=False)
    return path


def _init():
    initialize_paper_account("1000", RiskLimits(
        max_order_notional=10_000, max_position_notional=10_000,
        max_daily_loss=1_000, max_gross_exposure=10_000,
    ))


def _working(client_order_id="buy-1", **overrides):
    payload = dict(
        client_order_id=client_order_id, symbol="BTC", side="BUY",
        quantity="10", price="100", fee="0", observed_at=FRESH, now=NOW, working=True,
    )
    payload.update(overrides)
    return submit_paper_order(**payload)


def test_live_execution_stays_disabled():
    assert LIVE_EXECUTION_ENABLED is False


def test_sequences_are_monotonic_per_stream(db):
    bus = PaperEventBus(db, max_pending=8)
    for index in range(4):
        bus.publish("paper.orders", "order_decided", {"n": index}, event_id=f"order-{index}")
    page = bus.pull("desk", "paper.orders", limit=10)
    assert page["status"] == "ok"
    assert page["signal"] is None
    assert [event["seq"] for event in page["events"]] == [1, 2, 3, 4]
    assert [event["payload"]["n"] for event in page["events"]] == [0, 1, 2, 3]
    assert detect_gaps(page["events"], expected_seq=1) == []
    fills = bus.pull("desk", "paper.fills", limit=10)
    assert fills["events"] == []
    assert fills["head_seq"] == 0


def test_duplicate_event_id_does_not_advance_the_sequence(db):
    bus = PaperEventBus(db, max_pending=8)
    first = bus.publish("paper.fills", "fill_recorded", {"qty": "1"}, event_id="fill-1")
    second = bus.publish("paper.fills", "fill_recorded", {"qty": "9"}, event_id="fill-1")
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["seq"] == first["seq"]
    assert second["payload"]["qty"] == "1"
    page = bus.pull("desk", "paper.fills", limit=10)
    assert [event["event_id"] for event in page["events"]] == ["fill-1"]
    assert bus.head("paper.fills") == 1


def test_unacked_pull_redelivers_and_consumer_dedupes(db):
    bus = PaperEventBus(db, max_pending=8)
    for index in range(3):
        bus.publish("paper.orders", "order_decided", {"n": index}, event_id=f"e{index}")
    first = bus.pull("desk", "paper.orders", limit=10)
    second = bus.pull("desk", "paper.orders", limit=10)
    assert [event["event_id"] for event in first["events"]] == ["e0", "e1", "e2"]
    assert [event["event_id"] for event in second["events"]] == ["e0", "e1", "e2"]
    assert len(dedupe_events(first["events"] + second["events"])) == 3
    acked = bus.ack("desk", "paper.orders", 3)
    assert acked["acked_seq"] == 3
    assert acked["lag"] == 0
    again = bus.ack("desk", "paper.orders", 3)
    assert again["duplicate"] is True
    assert bus.pull("desk", "paper.orders", limit=10)["events"] == []


def test_ack_cannot_pass_the_head_and_lower_ack_is_a_no_op(db):
    bus = PaperEventBus(db, max_pending=8)
    bus.publish("paper.orders", "order_decided", {"n": 1}, event_id="only")
    bus.pull("desk", "paper.orders", limit=10)
    with pytest.raises(ValueError, match="ack_ahead_of_head"):
        bus.ack("desk", "paper.orders", 2)
    bus.ack("desk", "paper.orders", 1)
    stalled = bus.ack("desk", "paper.orders", 0)
    assert stalled["acked_seq"] == 1
    assert stalled["duplicate"] is True


def test_replay_from_offset_after_disconnect(db):
    bus = PaperEventBus(db, max_pending=8)
    bus.subscribe_live("desk", "paper.orders")
    for index in range(4):
        bus.publish("paper.orders", "order_decided", {"n": index}, event_id=f"e{index}")
    bus.ack("desk", "paper.orders", 2)
    bus.disconnect("desk", "paper.orders")
    bus.publish("paper.orders", "order_cancelled", {"n": 4}, event_id="e4")
    with pytest.raises(ValueError, match="not_subscribed"):
        bus.read_live("desk", "paper.orders")
    bus.subscribe_live("desk", "paper.orders")
    page = bus.replay("desk", "paper.orders", from_seq=2, limit=10)
    assert [event["seq"] for event in page["events"]] == [3, 4, 5]
    assert [event["event_id"] for event in page["events"]] == ["e2", "e3", "e4"]
    assert page["gaps"] == []
    assert page["live_trading_authorized"] is False
    assert page["live_execution"] is False


def test_slow_consumer_gets_resync_signal_and_retains_events(db):
    bus = PaperEventBus(db, max_pending=2)
    bus.subscribe_live("desk", "paper.orders")
    for index in range(3):
        bus.publish("paper.orders", "order_decided", {"n": index}, event_id=f"e{index}")
    live = bus.read_live("desk", "paper.orders")
    assert live["status"] == "resync_required"
    assert live["signal"] == "drop_to_resync"
    assert live["events"] == []
    assert live["retained"] is True
    assert live["dropped"] >= 1
    incremental = bus.pull("desk", "paper.orders", limit=10)
    assert incremental["signal"] == "drop_to_resync"
    assert incremental["events"] == []
    assert incremental["replay_from"] == 0
    recovered = bus.replay("desk", "paper.orders", from_seq=0, limit=10)
    assert [event["payload"]["n"] for event in recovered["events"]] == [0, 1, 2]
    assert recovered["status"] == "ok"


def test_pull_is_bounded_by_limit(db):
    bus = PaperEventBus(db, max_pending=50)
    for index in range(5):
        bus.publish("paper.marks", "mark_to_market", {"n": index}, event_id=f"m{index}")
    page = bus.pull("desk", "paper.marks", limit=2)
    assert [event["seq"] for event in page["events"]] == [1, 2]
    assert page["lag"] == 5
    with pytest.raises(ValueError, match="invalid_limit"):
        bus.pull("desk", "paper.marks", limit=10_000)


def test_consumer_detects_a_sequence_gap(db):
    bus = PaperEventBus(db, max_pending=8)
    for index in range(4):
        bus.publish("paper.risk", "kill_switch_set", {"n": index}, event_id=f"k{index}")
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM paper_event_log WHERE stream=? AND seq=?", ("paper.risk", 2))
    page = bus.replay("risk-desk", "paper.risk", from_seq=0, limit=10)
    assert [event["seq"] for event in page["events"]] == [1, 3, 4]
    assert page["gaps"] == [{"from_seq": 2, "to_seq": 2}]
    assert detect_gaps(page["events"], expected_seq=1) == [{"from_seq": 2, "to_seq": 2}]


def test_committed_events_and_offsets_survive_process_restart(db):
    bus = PaperEventBus(db, max_pending=8)
    bus.publish("paper.orders", "order_decided", {"n": 1}, event_id="kept")
    bus.publish("paper.fills", "fill_recorded", {"qty": "1"}, event_id="fill-kept")
    bus.pull("desk", "paper.orders", limit=10)
    bus.ack("desk", "paper.orders", 1)
    bus.forget_process_memory()
    restarted = PaperEventBus(db, max_pending=8)
    health = restarted.health()
    orders = next(row for row in health["consumers"] if row["stream"] == "paper.orders")
    assert orders["acked_seq"] == 1
    assert orders["lag"] == 0
    assert orders["head_seq"] == 1
    fills = restarted.replay("desk", "paper.fills", from_seq=0, limit=10)
    assert [event["event_id"] for event in fills["events"]] == ["fill-kept"]
    assert health["live_trading_authorized"] is False
    assert health["live_execution"] is False
    assert health["broker_order_submitted"] is False


def test_crash_before_commit_does_not_publish(db):
    _init()
    with pytest.raises(RuntimeError, match="simulated_crash_before_commit"):
        _working(simulate_crash=True)
    bus = PaperEventBus(db)
    assert bus.head("paper.orders") == 0
    lifecycle = bus.replay("ops", "paper.lifecycle", from_seq=0, limit=10)
    assert [event["event_type"] for event in lifecycle["events"]] == ["account_initialized"]
    assert bus.replay("ops", "paper.fills", from_seq=0, limit=10)["events"] == []


def test_paper_order_lifecycle_and_fills_use_the_bus(db):
    _init()
    _working()
    apply_paper_fill(
        client_order_id="buy-1", client_fill_id="fill-1", quantity="4",
        observed_at=FRESH, now=NOW,
    )
    apply_paper_fill(
        client_order_id="buy-1", client_fill_id="fill-1", quantity="4",
        observed_at=FRESH, now=NOW,
    )
    cancel_paper_order(client_order_id="buy-1")
    bus = PaperEventBus(db)
    orders = bus.pull("ledger", "paper.orders", limit=20)
    fills = bus.pull("ledger", "paper.fills", limit=20)
    # The repeated fill is an idempotent replay, not a second fill.
    assert [event["event_type"] for event in orders["events"]] == [
        "order_decided", "idempotent_replay", "order_cancelled",
    ]
    assert [event["seq"] for event in orders["events"]] == [1, 2, 3]
    assert orders["events"][0]["payload"]["state"] == "accepted"
    assert orders["events"][0]["client_order_id"] == "buy-1"
    assert [event["event_type"] for event in fills["events"]] == ["fill_recorded"]
    assert fills["events"][0]["payload"]["client_fill_id"] == "fill-1"
    assert fills["events"][0]["event_id"].startswith("journal:")
    retry = bus.pull("ledger", "paper.fills", limit=20)
    assert len(dedupe_events(fills["events"] + retry["events"])) == 1
    assert all(event["live_execution"] is False for event in orders["events"] + fills["events"])
    assert all(event["broker_order_submitted"] is False for event in orders["events"])


def test_mark_risk_and_reconciliation_events_are_published(db):
    _init()
    _working()
    mark_to_market({"BTC": "110"}, observed_at=FRESH, now=NOW)
    set_kill_switch(True)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE paper_exec_meta SET cash=? WHERE id=1", ("1",))
    report = reconciliation_report(record_mismatch=True)
    assert report["passed"] is False
    bus = PaperEventBus(db)
    marks = bus.replay("ops", "paper.marks", from_seq=0, limit=10)
    risk = bus.replay("ops", "paper.risk", from_seq=0, limit=10)
    recon = bus.replay("ops", "paper.reconciliation", from_seq=0, limit=10)
    assert [event["event_type"] for event in marks["events"]] == ["mark_to_market"]
    assert marks["events"][0]["payload"]["marks"]["BTC"] == "110"
    assert [event["event_type"] for event in risk["events"]] == ["kill_switch_set"]
    assert risk["events"][0]["payload"]["active"] is True
    assert [event["event_type"] for event in recon["events"]] == ["reconciliation_mismatch"]
    health = bus.health()
    by_stream = {row["stream"]: row["head_seq"] for row in health["streams"]}
    assert by_stream["paper.marks"] == 1
    assert by_stream["paper.risk"] == 1
    assert by_stream["paper.reconciliation"] == 1
    consumers = {(row["consumer_id"], row["stream"]): row["lag"] for row in health["consumers"]}
    assert consumers[("ops", "paper.marks")] == 1
    assert consumers[("ops", "paper.reconciliation")] == 1


def test_http_event_bus_view_is_paper_only(db):
    _init()
    _working("http-1", quantity="4")
    client = TestClient(app)
    view = client.get("/paper/execution/event-bus")
    assert view.status_code == 200
    body = view.json()
    assert body["live_trading_authorized"] is False
    assert body["live_execution"] is False
    assert body["broker_order_submitted"] is False
    orders = client.get("/paper/execution/events/paper.orders", params={"consumer": "http-desk", "limit": 10})
    assert orders.status_code == 200
    assert [event["event_type"] for event in orders.json()["events"]] == ["order_decided"]
    ack = client.post("/paper/execution/events/paper.orders/ack", json={"consumer_id": "http-desk", "seq": 1})
    assert ack.status_code == 200
    assert ack.json()["lag"] == 0
    replay = client.get(
        "/paper/execution/events/paper.orders/replay",
        params={"consumer": "http-desk", "from_seq": 0, "limit": 10},
    )
    assert [event["seq"] for event in replay.json()["events"]] == [1]
    assert client.get("/health").json()["live_execution"] is False
