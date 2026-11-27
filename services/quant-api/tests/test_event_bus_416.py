"""Package 42: paper order and fill events need a reliable local bus.

On main, before this module existed, consumers of paper order, fill, mark,
risk and reconciliation events had no per-stream sequence, no ack offset, and
no replay. The tests below are the contract those gaps failed.
"""
from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app
from osiris_api.paper_event_bus import (
    PaperEventBus,
    dedupe_events,
    detect_gaps,
    dispatch_committed,
)
from osiris_api.paper_execution import (
    LIVE_EXECUTION_ENABLED,
    apply_paper_fill,
    broker_book_digest,
    cancel_paper_order,
    expire_paper_orders,
    initialize_paper_account,
    mark_to_market,
    reconciliation_report,
    reconcile_broker_book,
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


@pytest.mark.parametrize("release_before_read", [False, True])
def test_racing_commit_notifications_require_ordered_replay(db, monkeypatch, release_before_read):
    from osiris_api import paper_event_bus

    bus = PaperEventBus(db)
    bus.subscribe_live("desk", "paper.orders")
    first_committed, release_first = Event(), Event()
    original = paper_event_bus.dispatch_committed

    def delayed_dispatch(path, events):
        if events[0]["seq"] == 1:
            first_committed.set()
            assert release_first.wait(5)
        original(path, events)

    monkeypatch.setattr(paper_event_bus, "dispatch_committed", delayed_dispatch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(
            bus.publish, "paper.orders", "order_decided", {"state": "accepted"}, "accepted",
        )
        try:
            assert first_committed.wait(5)
            bus.publish("paper.orders", "order_cancelled", {"state": "cancelled"}, "cancelled")
            if release_before_read:
                release_first.set()
                first.result(timeout=5)
            page = bus.read_live("desk", "paper.orders")
            assert page["events"] == []
            assert page["signal"] == "drop_to_resync"
        finally:
            release_first.set()
        first.result(timeout=5)

    assert bus.read_live("desk", "paper.orders")["signal"] == "drop_to_resync"
    replay = bus.replay("desk", "paper.orders", from_seq=0)
    assert [event["seq"] for event in replay["events"]] == [1, 2]
    assert replay["next_from"] == 2
    bus.ack("desk", "paper.orders", 2)
    bus.publish("paper.orders", "order_decided", {"state": "accepted"}, "next-order")
    assert [event["seq"] for event in bus.read_live("desk", "paper.orders")["events"]] == [3]


def test_duplicate_live_notifications_cannot_move_delivery_cursor_backwards(db):
    bus = PaperEventBus(db)
    bus.subscribe_live("desk", "paper.orders")
    first = bus.publish("paper.orders", "order_decided", {"state": "accepted"}, "accepted")
    bus.publish("paper.orders", "order_cancelled", {"state": "cancelled"}, "cancelled")
    dispatch_committed(str(db), [{key: value for key, value in first.items() if key != "duplicate"}])
    page = bus.read_live("desk", "paper.orders")
    assert [event["seq"] for event in page["events"]] == [1, 2]
    assert page["next_from"] == 2
    dispatch_committed(str(db), [{key: value for key, value in first.items() if key != "duplicate"}])
    assert bus.read_live("desk", "paper.orders")["events"] == []


def test_subscription_baseline_excludes_delayed_historical_notification(db, monkeypatch):
    from osiris_api import paper_event_bus

    bus = PaperEventBus(db)
    first_committed, release_first = Event(), Event()
    original = paper_event_bus.dispatch_committed

    def delayed_dispatch(path, events):
        if events[0]["seq"] == 1:
            first_committed.set()
            assert release_first.wait(5)
        original(path, events)

    monkeypatch.setattr(paper_event_bus, "dispatch_committed", delayed_dispatch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(bus.publish, "paper.orders", "order_decided", {"n": 1}, "e1")
        try:
            assert first_committed.wait(5)
            assert bus.subscribe_live("desk", "paper.orders")["head_seq"] == 1
        finally:
            release_first.set()
        first.result(timeout=5)
    bus.publish("paper.orders", "order_cancelled", {"n": 2}, "e2")
    page = bus.read_live("desk", "paper.orders")
    assert page["signal"] is None
    assert [event["seq"] for event in page["events"]] == [2]
    assert page["gaps"] == []


def test_failed_replay_does_not_clear_live_resync(db):
    _init()
    bus = PaperEventBus(db, max_pending=1)
    bus.subscribe_live("desk", "paper.orders")
    _working("first", quantity="1")
    _working("second", quantity="1")
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE paper_event_log SET event_type='forged' WHERE stream='paper.orders'")
    with pytest.raises(ValueError, match="event_journal_integrity_failed"):
        bus.replay("desk", "paper.orders", from_seq=0)
    assert bus.read_live("desk", "paper.orders")["signal"] == "drop_to_resync"


def test_paginated_replay_keeps_resync_until_every_page_is_delivered(db):
    bus = PaperEventBus(db, max_pending=2)
    bus.subscribe_live("desk", "paper.orders")
    for seq in range(1, 4):
        bus.publish("paper.orders", "order_decided", {"n": seq}, f"e{seq}")
    assert bus.read_live("desk", "paper.orders")["signal"] == "drop_to_resync"

    for seq in range(1, 4):
        page = bus.replay("desk", "paper.orders", from_seq=seq - 1, limit=1)
        assert [event["seq"] for event in page["events"]] == [seq]
        assert page["next_from"] == seq
        assert page["gaps"] == []
        bus.ack("desk", "paper.orders", seq)
        live = bus.read_live("desk", "paper.orders")
        assert live["events"] == []
        assert live["signal"] == ("drop_to_resync" if seq < 3 else None)

    bus.publish("paper.orders", "order_cancelled", {"n": 4}, "e4")
    live = bus.read_live("desk", "paper.orders")
    assert [event["seq"] for event in live["events"]] == [4]
    assert live["gaps"] == []
    assert live["signal"] is None


def test_partial_replay_preserves_undelivered_live_notifications(db):
    bus = PaperEventBus(db, max_pending=8)
    bus.subscribe_live("desk", "paper.orders")
    for seq in range(1, 4):
        bus.publish("paper.orders", "order_decided", {"n": seq}, f"e{seq}")

    page = bus.replay("desk", "paper.orders", from_seq=0, limit=1)
    assert [event["seq"] for event in page["events"]] == [1]
    bus.ack("desk", "paper.orders", 1)
    live = bus.read_live("desk", "paper.orders")
    assert [event["seq"] for event in live["events"]] == [2, 3]
    assert live["signal"] is None
    assert live["gaps"] == []


def test_replay_beyond_head_cannot_clear_undelivered_resync(db):
    bus = PaperEventBus(db, max_pending=2)
    bus.subscribe_live("desk", "paper.orders")
    for seq in range(1, 4):
        bus.publish("paper.orders", "order_decided", {"n": seq}, f"e{seq}")
    assert bus.read_live("desk", "paper.orders")["signal"] == "drop_to_resync"

    page = bus.replay("desk", "paper.orders", from_seq=4, limit=1)
    assert page["events"] == []
    live = bus.read_live("desk", "paper.orders")
    assert live["events"] == []
    assert live["signal"] == "drop_to_resync"
    assert live["acked_seq"] == 0
    recovered = bus.replay("desk", "paper.orders", from_seq=0)
    assert [event["seq"] for event in recovered["events"]] == [1, 2, 3]


def test_replay_skipping_undelivered_prefix_keeps_resync(db):
    bus = PaperEventBus(db, max_pending=2)
    bus.subscribe_live("desk", "paper.orders")
    for seq in range(1, 4):
        bus.publish("paper.orders", "order_decided", {"n": seq}, f"e{seq}")
    assert bus.read_live("desk", "paper.orders")["signal"] == "drop_to_resync"

    skipped = bus.replay("desk", "paper.orders", from_seq=1)
    assert [event["seq"] for event in skipped["events"]] == [2, 3]
    assert skipped["gaps"] == []
    assert bus.read_live("desk", "paper.orders")["signal"] == "drop_to_resync"
    recovered = bus.replay("desk", "paper.orders", from_seq=0)
    assert [event["seq"] for event in recovered["events"]] == [1, 2, 3]
    assert bus.read_live("desk", "paper.orders")["signal"] is None


def test_replay_with_a_durable_sequence_gap_keeps_resync(db):
    bus = PaperEventBus(db, max_pending=2)
    bus.subscribe_live("desk", "paper.orders")
    for seq in range(1, 4):
        bus.publish("paper.orders", "order_decided", {"n": seq}, f"e{seq}")
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM paper_event_log WHERE stream='paper.orders' AND seq=2")

    page = bus.replay("desk", "paper.orders", from_seq=0)
    assert [event["seq"] for event in page["events"]] == [1, 3]
    assert page["gaps"] == [{"from_seq": 2, "to_seq": 2}]
    assert bus.read_live("desk", "paper.orders")["signal"] == "drop_to_resync"


def test_empty_replay_at_delivered_head_can_clear_duplicate_overflow(db):
    bus = PaperEventBus(db, max_pending=1)
    bus.subscribe_live("desk", "paper.orders")
    event = bus.publish("paper.orders", "order_decided", {"n": 1}, "e1")
    assert [item["seq"] for item in bus.read_live("desk", "paper.orders")["events"]] == [1]
    bus.ack("desk", "paper.orders", 1)
    notification = {key: value for key, value in event.items() if key != "duplicate"}
    dispatch_committed(str(db), [notification, notification])
    assert bus.read_live("desk", "paper.orders")["signal"] == "drop_to_resync"

    assert bus.replay("desk", "paper.orders", from_seq=1)["events"] == []
    assert bus.read_live("desk", "paper.orders")["signal"] is None
    bus.publish("paper.orders", "order_cancelled", {"n": 2}, "e2")
    page = bus.read_live("desk", "paper.orders")
    assert [item["seq"] for item in page["events"]] == [2]
    assert page["gaps"] == []


def test_replay_from_acknowledged_pull_recovers_live_overflow(db):
    bus = PaperEventBus(db, max_pending=2)
    bus.subscribe_live("desk", "paper.orders")
    for seq in range(1, 3):
        bus.publish("paper.orders", "order_decided", {"n": seq}, f"e{seq}")
    page = bus.pull("desk", "paper.orders")
    assert [item["seq"] for item in page["events"]] == [1, 2]
    bus.ack("desk", "paper.orders", 2)
    bus.publish("paper.orders", "order_decided", {"n": 3}, "e3")
    assert bus.read_live("desk", "paper.orders")["signal"] == "drop_to_resync"

    page = bus.replay("desk", "paper.orders", from_seq=2)
    assert [item["seq"] for item in page["events"]] == [3]
    assert bus.read_live("desk", "paper.orders")["signal"] is None
    bus.publish("paper.orders", "order_cancelled", {"n": 4}, "e4")
    live = bus.read_live("desk", "paper.orders")
    assert [item["seq"] for item in live["events"]] == [4]
    assert live["gaps"] == []


def test_replay_does_not_clear_newer_overflow_after_its_snapshot(db, monkeypatch):
    bus = PaperEventBus(db, max_pending=1)
    writer = PaperEventBus(db)
    bus.subscribe_live("desk", "paper.orders")
    writer.publish("paper.orders", "order_decided", {"n": 1}, "e1")
    connect = bus._connect

    class PublishAfterCommit:
        def __init__(self):
            self.conn = connect()

        def __getattr__(self, name):
            return getattr(self.conn, name)

        def commit(self):
            self.conn.commit()
            # These commits are newer than replay's head=1 snapshot. Its
            # successful read cannot recover their overflowed notifications.
            writer.publish("paper.orders", "order_decided", {"n": 2}, "e2")
            writer.publish("paper.orders", "order_decided", {"n": 3}, "e3")

    monkeypatch.setattr(bus, "_connect", PublishAfterCommit)
    replay = bus.replay("desk", "paper.orders", from_seq=0)
    assert [event["seq"] for event in replay["events"]] == [1]
    monkeypatch.setattr(bus, "_connect", connect)
    assert bus.read_live("desk", "paper.orders")["signal"] == "drop_to_resync"
    recovered = bus.replay("desk", "paper.orders", from_seq=1)
    assert [event["seq"] for event in recovered["events"]] == [2, 3]
    assert bus.read_live("desk", "paper.orders")["signal"] is None


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


def test_expiry_and_broker_reconciliation_notify_only_after_commit(db, tmp_path, monkeypatch):
    """New lifecycle paths use the same durable/live bus transaction boundary."""
    _init()
    _working()
    apply_paper_fill(
        client_order_id="buy-1", client_fill_id="fill-1", quantity="4",
        observed_at=FRESH, now=NOW,
    )
    bus = PaperEventBus(db)
    bus.subscribe_live("ops", "paper.orders")
    bus.subscribe_live("ops", "paper.reconciliation")
    expiry = "2026-11-09T00:00:00+00:00"
    with pytest.raises(RuntimeError, match="simulated_crash_before_commit"):
        expire_paper_orders(now=expiry, simulate_crash=True)
    assert bus.read_live("ops", "paper.orders")["events"] == []
    before = bus.replay("audit", "paper.orders", from_seq=0, limit=20)["events"]
    assert [row["event_type"] for row in before] == ["order_decided"]

    assert expire_paper_orders(now=expiry)["expired"] == ["buy-1"]
    expired = bus.read_live("ops", "paper.orders")["events"]
    assert [row["event_type"] for row in expired] == ["order_expired"]
    assert expired[0]["payload"]["filled_quantity"] == "4"
    with pytest.raises(ValueError, match="order_expired"):
        cancel_paper_order(client_order_id="buy-1", now=expiry)
    cancelled = bus.read_live("ops", "paper.orders")["events"]
    assert [row["event_type"] for row in cancelled] == ["cancel_rejected"]

    book = {
        "schema": "osiris.paper_broker_book.v1", "mode": "paper", "as_of": expiry,
        "cash": "600", "buying_power": "600",
        "positions": [{"symbol": "BTC", "quantity": "4"}],
        "fills": [{
            "execution_id": "fill-1", "client_order_id": "buy-1", "symbol": "BTC",
            "side": "BUY", "quantity": "4", "price": "100",
        }],
        "open_orders": [],
    }
    book["digest"] = broker_book_digest(book)
    capture = tmp_path / "broker-book.json"
    capture.write_text(json.dumps(book), encoding="utf-8")
    monkeypatch.setenv("OSIRIS_PAPER_BROKER_SNAPSHOT", str(capture))
    monkeypatch.delenv("OSIRIS_CONTROL_DB", raising=False)
    assert reconcile_broker_book(now=expiry)["status"] == "clean"
    notices = bus.read_live("ops", "paper.reconciliation")["events"]
    assert [row["event_type"] for row in notices] == ["broker_reconciliation"]
    assert notices[0]["payload"]["status"] == "clean"
    retained = bus.replay("audit", "paper.reconciliation", from_seq=0, limit=20)["events"]
    assert [row["event_id"] for row in retained] == [notices[0]["event_id"]]
    assert reconcile_broker_book(now=expiry)["persisted"] is False
    assert bus.read_live("ops", "paper.reconciliation")["events"] == []
    assert bus.read_live("ops", "paper.orders")["events"] == []


@pytest.mark.parametrize("change", [
    "payload", "event_type", "client_order_id", "journal_seq", "missing_reference",
    "event_id", "stream",
])
def test_journal_delivery_refuses_tampered_bus_copy(db, change):
    _init()
    bus = PaperEventBus(db)
    bus.subscribe_live("live", "paper.orders")
    _working()
    stream = "paper.orders"
    with sqlite3.connect(db) as conn:
        if change == "payload":
            payload = json.loads(conn.execute(
                "SELECT payload FROM paper_event_log WHERE stream=?", (stream,),
            ).fetchone()[0])
            payload["price"] = "999999"
            value = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            field = "payload"
        else:
            field = "journal_seq" if change == "missing_reference" else change
            value = {
                "event_type": "order_cancelled", "client_order_id": "other-order",
                "journal_seq": 1, "missing_reference": None, "event_id": "forged-id",
                "stream": "paper.audit",
            }[change]
        conn.execute(f"UPDATE paper_event_log SET {field}=? WHERE stream=?", (value, stream))
    # The canonical order remains sound; only the delivery projection changed.
    assert reconciliation_report()["passed"] is True
    if change == "stream":
        stream = "paper.audit"
    with pytest.raises(ValueError, match="event_journal_integrity_failed"):
        bus.pull("pull", stream)
    with pytest.raises(ValueError, match="event_journal_integrity_failed"):
        bus.replay("replay", stream, from_seq=0)
    with pytest.raises(ValueError, match="event_journal_integrity_failed"):
        bus.read_live("live", "paper.orders")


def test_journal_delivery_refuses_corrupt_canonical_chain(db):
    _init()
    bus = PaperEventBus(db)
    bus.subscribe_live("live", "paper.orders")
    _working()
    # The delivery copy still matches its row. An unrelated broken journal link
    # must nevertheless invalidate the batch's claimed canonical provenance.
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE paper_exec_audit SET row_hash='corrupt' WHERE seq=1")
    with pytest.raises(ValueError, match="event_journal_integrity_failed"):
        bus.pull("pull", "paper.orders")
    with pytest.raises(ValueError, match="event_journal_integrity_failed"):
        bus.replay("replay", "paper.orders", from_seq=0)
    with pytest.raises(ValueError, match="event_journal_integrity_failed"):
        bus.read_live("live", "paper.orders")


def test_forged_live_journal_notification_cannot_override_retained_event(db):
    _init()
    _working()
    bus = PaperEventBus(db)
    original = bus.replay("audit", "paper.orders", from_seq=0)["events"][0]
    bus.subscribe_live("live", "paper.orders")
    forged = {**original, "payload": {**original["payload"], "price": "999999"}}
    dispatch_committed(str(db), [forged])
    with pytest.raises(ValueError, match="event_journal_integrity_failed"):
        bus.read_live("live", "paper.orders")
    assert bus.pull("audit", "paper.orders")["events"][0]["payload"]["price"] == "100"


def test_journal_chain_is_verified_once_for_a_delivery_batch(db, monkeypatch):
    from osiris_api import paper_execution

    _init()
    _working("one", quantity="1")
    _working("two", quantity="1")
    original = paper_execution._verify_audit
    verified = []

    def verify_once(conn):
        verified.append(True)
        original(conn)

    monkeypatch.setattr(paper_execution, "_verify_audit", verify_once)
    assert len(PaperEventBus(db).pull("audit", "paper.orders")["events"]) == 2
    assert len(verified) == 1


def test_http_delivery_refuses_tampered_journal_projection(db):
    _init()
    _working()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE paper_event_log SET event_type='order_cancelled' WHERE stream='paper.orders'",
        )
    response = TestClient(app).get(
        "/paper/execution/events/paper.orders", params={"consumer": "review"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "event_journal_integrity_failed"
