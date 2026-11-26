"""Durable local bus for paper order, fill, mark, risk and reconciliation events.

The paper journal already appends a hash chain. Consumers previously had to
re-read the whole snapshot: no per-stream sequence, no acknowledgement offset,
no replay after a disconnect, and no bound on what a slow reader was handed.
This bus is that delivery layer. It lives in the same SQLite file as the
journal and is written in the journal's transaction, so a crash before commit
publishes nothing.

Delivery is at least once. A consumer acknowledges a sequence offset. Until
that ack, a pull returns the same events again. ``event_id`` is stable so the
consumer can drop duplicates. A full in-memory queue, or a pull whose lag is
past ``max_pending``, returns ``drop_to_resync`` and keeps the rows. Replay
from the last ack is how the consumer catches up. Nothing here is deleted to
make room.

There is no network broker. Live trading stays disabled.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import deque
from pathlib import Path
from threading import Lock
from typing import Any

SCHEMA = "osiris.paper_event_bus.v1"
DEFAULT_MAX_PENDING = 256
MAX_PULL_LIMIT = 500
MAX_PAYLOAD_BYTES = 65_536

STREAMS = (
    "paper.lifecycle",
    "paper.orders",
    "paper.fills",
    "paper.marks",
    "paper.risk",
    "paper.reconciliation",
    "paper.audit",
)
_STREAMS = frozenset(STREAMS)
_EVENT_STREAM = {
    "account_initialized": "paper.lifecycle",
    "order_decided": "paper.orders",
    "order_cancelled": "paper.orders",
    "order_expired": "paper.orders",
    "cancel_rejected": "paper.orders",
    "cancel_replay": "paper.orders",
    "invalid_order_transition": "paper.orders",
    "idempotent_replay": "paper.orders",
    "idempotency_conflict": "paper.orders",
    "fill_recorded": "paper.fills",
    "fill_rejected": "paper.fills",
    "mark_to_market": "paper.marks",
    "kill_switch_set": "paper.risk",
    "live_execution_refused": "paper.risk",
    "reconciliation_mismatch": "paper.reconciliation",
    "broker_reconciliation": "paper.reconciliation",
}
_CONSUMER_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")
_EVENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_EVENT_TYPE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

class _LiveQueue:
    """Bounded handoff. Overflow clears the buffer and asks for a resync.

    A ``deque(maxlen=...)`` would drop the oldest event and keep serving the
    rest, so the consumer would never notice the hole. This queue stops
    instead. The SQLite log still has every event.
    """

    def __init__(self, max_pending: int):
        self.max_pending = max_pending
        self.items: deque[dict[str, Any]] = deque()
        self.resync = False
        self.dropped = 0

    def offer(self, event: dict[str, Any]) -> None:
        if self.resync or len(self.items) >= self.max_pending:
            self.dropped += len(self.items) + 1
            self.items.clear()
            self.resync = True
            return
        self.items.append(event)

    def take(self) -> tuple[str, list[dict[str, Any]], int]:
        if self.resync:
            return "resync", [], self.dropped
        events = list(self.items)
        self.items.clear()
        return "ok", events, self.dropped


_LIVE_LOCK = Lock()
_LIVE: dict[tuple[str, str, str], _LiveQueue] = {}
_PENDING_LOCK = Lock()
_PENDING: dict[int, list[dict[str, Any]]] = {}
_dispatch_failures = 0


def _resolve(path: str) -> str:
    return str(Path(path).resolve())


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _check_stream(stream: str) -> str:
    if stream not in _STREAMS:
        raise ValueError("invalid_stream")
    return stream


def _check_consumer(consumer_id: str) -> str:
    if not isinstance(consumer_id, str) or _CONSUMER_ID.fullmatch(consumer_id) is None:
        raise ValueError("invalid_consumer_id")
    return consumer_id


def _check_limit(limit: int) -> int:
    if type(limit) is not int or not 1 <= limit <= MAX_PULL_LIMIT:
        raise ValueError("invalid_limit")
    return limit


def _check_sequence(seq: int, *, allow_zero: bool) -> int:
    if type(seq) is not int or seq < 0 or (seq == 0 and not allow_zero):
        raise ValueError("invalid_sequence")
    return seq


def _check_event_id(event_id: str) -> str:
    if (
        not isinstance(event_id, str)
        or event_id.startswith("journal:")
        or _EVENT_ID.fullmatch(event_id) is None
    ):
        raise ValueError("invalid_event_id")
    return event_id


def _check_event_type(event_type: str) -> str:
    if not isinstance(event_type, str) or _EVENT_TYPE.fullmatch(event_type) is None:
        raise ValueError("invalid_event_type")
    return event_type


def _check_payload(payload: Any, *, limit_bytes: bool) -> str:
    if not isinstance(payload, dict):
        raise ValueError("invalid_event_payload")
    try:
        encoded = _canonical(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_event_payload") from exc
    if limit_bytes and len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("invalid_event_payload")
    return encoded


def detect_gaps(events: list[dict[str, Any]], *, expected_seq: int) -> list[dict[str, int]]:
    """Holes in a delivered page. ``expected_seq`` is the first sequence the page should hold."""
    if type(expected_seq) is not int or expected_seq < 1:
        raise ValueError("invalid_sequence")
    if not isinstance(events, list):
        raise ValueError("invalid_event_payload")
    gaps: list[dict[str, int]] = []
    cursor = expected_seq
    for event in events:
        if not isinstance(event, dict) or type(event.get("seq")) is not int or event["seq"] < 1:
            raise ValueError("invalid_sequence")
        seq = event["seq"]
        if seq != cursor and seq > cursor:
            gaps.append({"from_seq": cursor, "to_seq": seq - 1})
        cursor = seq + 1
    return gaps


def dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the first copy of each ``event_id``. Later copies are redeliveries."""
    if not isinstance(events, list):
        raise ValueError("invalid_event_payload")
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("event_id"), str):
            raise ValueError("invalid_event_payload")
        if event["event_id"] in seen:
            continue
        seen.add(event["event_id"])
        kept.append(event)
    return kept


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS paper_event_log (
            stream TEXT NOT NULL,
            seq INTEGER NOT NULL CHECK(seq > 0),
            event_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            journal_seq INTEGER,
            client_order_id TEXT,
            payload TEXT NOT NULL,
            PRIMARY KEY (stream, seq),
            UNIQUE (event_id)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS paper_event_consumer (
            consumer_id TEXT NOT NULL,
            stream TEXT NOT NULL,
            acked_seq INTEGER NOT NULL CHECK(acked_seq >= 0),
            status TEXT NOT NULL CHECK(status IN ('active', 'resync_required', 'disconnected')),
            dropped INTEGER NOT NULL CHECK(dropped >= 0),
            PRIMARY KEY (consumer_id, stream)
        )"""
    )


def _public_event(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "stream": row["stream"],
        "seq": int(row["seq"]),
        "event_id": row["event_id"],
        "event_type": row["event_type"],
        "journal_seq": None if row["journal_seq"] is None else int(row["journal_seq"]),
        "client_order_id": row["client_order_id"],
        "payload": json.loads(row["payload"]),
        "live_execution": False,
        "live_trading_authorized": False,
        "broker_order_submitted": False,
    }


def _insert_event(
    conn: sqlite3.Connection,
    *,
    stream: str,
    event_type: str,
    event_id: str,
    payload: str,
    journal_seq: int | None,
    client_order_id: str | None,
) -> tuple[dict[str, Any], bool]:
    existing = conn.execute(
        "SELECT * FROM paper_event_log WHERE event_id=?", (event_id,)
    ).fetchone()
    if existing is not None:
        if existing["stream"] != stream:
            raise ValueError("event_id_stream_conflict")
        return _public_event(existing), True
    previous = conn.execute(
        "SELECT seq FROM paper_event_log WHERE stream=? ORDER BY seq DESC LIMIT 1",
        (stream,),
    ).fetchone()
    seq = 1 if previous is None else int(previous["seq"]) + 1
    conn.execute(
        """INSERT INTO paper_event_log(
            stream, seq, event_id, event_type, journal_seq, client_order_id, payload)
           VALUES (?,?,?,?,?,?,?)""",
        (stream, seq, event_id, event_type, journal_seq, client_order_id, payload),
    )
    row = conn.execute(
        "SELECT * FROM paper_event_log WHERE stream=? AND seq=?", (stream, seq)
    ).fetchone()
    return _public_event(row), False


def stage_journal_event(
    conn: sqlite3.Connection,
    seq: int,
    event: str,
    client_order_id: str | None,
    payload: dict[str, Any],
) -> None:
    """Append one journal row's bus copy inside the caller's open transaction."""
    if type(seq) is not int or seq < 1:
        raise ValueError("invalid_sequence")
    if not isinstance(event, str) or not event or len(event) > 64:
        raise ValueError("invalid_event_type")
    if client_order_id is not None and not isinstance(client_order_id, str):
        raise ValueError("invalid_event_payload")
    ensure_schema(conn)
    stream = _EVENT_STREAM.get(event, "paper.audit")
    encoded = _check_payload(payload, limit_bytes=False)
    staged, duplicate = _insert_event(
        conn,
        stream=stream,
        event_type=event,
        event_id=f"journal:{seq}",
        payload=encoded,
        journal_seq=seq,
        client_order_id=client_order_id,
    )
    if duplicate:
        return
    with _PENDING_LOCK:
        _PENDING.setdefault(id(conn), []).append(staged)


def take_pending(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Detach events staged on this connection. Commit dispatches them; rollback drops them."""
    with _PENDING_LOCK:
        return _PENDING.pop(id(conn), [])


def dispatch_committed(path: str, events: list[dict[str, Any]]) -> None:
    """Hand committed events to in-memory subscribers. The log is already durable."""
    global _dispatch_failures
    try:
        resolved = _resolve(path)
        with _LIVE_LOCK:
            for event in events:
                stream = event.get("stream")
                for key, queue in _LIVE.items():
                    if key[0] == resolved and key[2] == stream:
                        queue.offer(dict(event))
    except Exception:
        _dispatch_failures += 1


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "available": False,
        "reason": reason,
        "streams": [],
        "consumers": [],
        "live_execution": False,
        "live_trading_authorized": False,
        "broker_order_submitted": False,
    }


def health_if_configured() -> dict[str, Any]:
    """Ops view that does not create a ledger file when none is configured."""
    path = os.getenv("OSIRIS_PAPER_EXECUTION_DB", "")
    if not path or path == ":memory:" or not Path(path).is_file():
        return _unavailable("paper_execution_db_not_configured")
    try:
        return PaperEventBus(path).health()
    except (OSError, sqlite3.Error, ValueError):
        return _unavailable("event_bus_unavailable")


class PaperEventBus:
    def __init__(self, path: str, *, max_pending: int = DEFAULT_MAX_PENDING):
        if not isinstance(path, str) and not isinstance(path, Path):
            raise ValueError("durable_file_path_required")
        text = str(path)
        if not text or text == ":memory:":
            raise ValueError("durable_file_path_required")
        if type(max_pending) is not int or not 1 <= max_pending <= 10_000:
            raise ValueError("invalid_max_pending")
        Path(text).parent.mkdir(parents=True, exist_ok=True)
        self.path = _resolve(text)
        self.max_pending = max_pending
        conn = self._connect()
        try:
            ensure_schema(conn)
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def head(self, stream: str) -> int:
        _check_stream(stream)
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            return _head(conn, stream)
        finally:
            conn.close()

    def publish(
        self,
        stream: str,
        event_type: str,
        payload: dict[str, Any],
        event_id: str,
        *,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        _check_stream(stream)
        _check_event_type(event_type)
        _check_event_id(event_id)
        if client_order_id is not None and (
            not isinstance(client_order_id, str) or not 1 <= len(client_order_id) <= 128
        ):
            raise ValueError("invalid_event_payload")
        encoded = _check_payload(payload, limit_bytes=True)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            ensure_schema(conn)
            event, duplicate = _insert_event(
                conn,
                stream=stream,
                event_type=event_type,
                event_id=event_id,
                payload=encoded,
                journal_seq=None,
                client_order_id=client_order_id,
            )
            if duplicate:
                conn.rollback()
            else:
                conn.commit()
                dispatch_committed(self.path, [event])
            return {**event, "duplicate": duplicate}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def subscribe_live(self, consumer_id: str, stream: str) -> dict[str, Any]:
        _check_consumer(consumer_id)
        _check_stream(stream)
        self._touch_consumer(consumer_id, stream, status="active")
        with _LIVE_LOCK:
            key = (self.path, consumer_id, stream)
            if key not in _LIVE:
                _LIVE[key] = _LiveQueue(self.max_pending)
        return self._consumer_view(consumer_id, stream)

    def disconnect(self, consumer_id: str, stream: str) -> dict[str, Any]:
        _check_consumer(consumer_id)
        _check_stream(stream)
        with _LIVE_LOCK:
            removed = _LIVE.pop((self.path, consumer_id, stream), None)
        if removed is None and not self._consumer_exists(consumer_id, stream):
            raise ValueError("unknown_consumer")
        self._set_status(consumer_id, stream, "disconnected")
        return self._consumer_view(consumer_id, stream)

    def read_live(self, consumer_id: str, stream: str) -> dict[str, Any]:
        _check_consumer(consumer_id)
        _check_stream(stream)
        with _LIVE_LOCK:
            queue = _LIVE.get((self.path, consumer_id, stream))
            if queue is None:
                raise ValueError("not_subscribed")
            kind, events, dropped = queue.take()
        view = self._consumer_view(consumer_id, stream)
        if kind == "resync":
            return _page(
                stream=stream,
                consumer_id=consumer_id,
                status="resync_required",
                signal="drop_to_resync",
                events=[],
                gaps=[],
                acked=view["acked_seq"],
                head=view["head_seq"],
                dropped=dropped,
                replay_from=view["acked_seq"],
            )
        return _page(
            stream=stream,
            consumer_id=consumer_id,
            status="ok",
            signal=None,
            events=events,
            gaps=detect_gaps(events, expected_seq=events[0]["seq"]) if events else [],
            acked=view["acked_seq"],
            head=view["head_seq"],
            dropped=dropped,
            replay_from=view["acked_seq"],
        )

    def pull(self, consumer_id: str, stream: str, *, limit: int = 100) -> dict[str, Any]:
        _check_consumer(consumer_id)
        _check_stream(stream)
        limit = _check_limit(limit)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            ensure_schema(conn)
            _ensure_consumer(conn, consumer_id, stream)
            head = _head(conn, stream)
            consumer = _consumer_row(conn, consumer_id, stream)
            acked = int(consumer["acked_seq"])
            lag = head - acked
            if lag > self.max_pending:
                conn.execute(
                    """UPDATE paper_event_consumer
                       SET status='resync_required', dropped=?
                       WHERE consumer_id=? AND stream=?""",
                    (lag, consumer_id, stream),
                )
                conn.commit()
                return _page(
                    stream=stream,
                    consumer_id=consumer_id,
                    status="resync_required",
                    signal="drop_to_resync",
                    events=[],
                    gaps=[],
                    acked=acked,
                    head=head,
                    dropped=lag,
                    replay_from=acked,
                )
            events = _after(conn, stream, acked, limit)
            gaps = detect_gaps(events, expected_seq=acked + 1) if events else []
            if consumer["status"] != "disconnected":
                conn.execute(
                    """UPDATE paper_event_consumer SET status='active'
                       WHERE consumer_id=? AND stream=?""",
                    (consumer_id, stream),
                )
            conn.commit()
            return _page(
                stream=stream,
                consumer_id=consumer_id,
                status="ok",
                signal=None,
                events=events,
                gaps=gaps,
                acked=acked,
                head=head,
                dropped=int(consumer["dropped"]),
                replay_from=acked,
            )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def replay(
        self, consumer_id: str, stream: str, *, from_seq: int, limit: int = 100,
    ) -> dict[str, Any]:
        """Explicit catch-up. This is allowed past ``max_pending`` and does not ack."""
        _check_consumer(consumer_id)
        _check_stream(stream)
        from_seq = _check_sequence(from_seq, allow_zero=True)
        limit = _check_limit(limit)
        with _LIVE_LOCK:
            queue = _LIVE.get((self.path, consumer_id, stream))
            if queue is not None:
                queue.resync = False
                queue.items.clear()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            ensure_schema(conn)
            _ensure_consumer(conn, consumer_id, stream)
            head = _head(conn, stream)
            consumer = _consumer_row(conn, consumer_id, stream)
            events = _after(conn, stream, from_seq, limit)
            gaps = detect_gaps(events, expected_seq=from_seq + 1) if events else []
            conn.commit()
            return _page(
                stream=stream,
                consumer_id=consumer_id,
                status="ok",
                signal=None,
                events=events,
                gaps=gaps,
                acked=int(consumer["acked_seq"]),
                head=head,
                dropped=int(consumer["dropped"]),
                replay_from=from_seq,
            )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def ack(self, consumer_id: str, stream: str, seq: int) -> dict[str, Any]:
        _check_consumer(consumer_id)
        _check_stream(stream)
        seq = _check_sequence(seq, allow_zero=True)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            ensure_schema(conn)
            consumer = _consumer_row(conn, consumer_id, stream)
            if consumer is None:
                raise ValueError("unknown_consumer")
            head = _head(conn, stream)
            acked = int(consumer["acked_seq"])
            if seq > head:
                raise ValueError("ack_ahead_of_head")
            if seq <= acked:
                conn.rollback()
                return _ack_body(
                    consumer_id, stream, acked=acked, head=head,
                    status=consumer["status"], duplicate=True,
                )
            status = consumer["status"]
            if status != "disconnected":
                status = "active" if head - seq <= self.max_pending else "resync_required"
            conn.execute(
                """UPDATE paper_event_consumer SET acked_seq=?, status=?
                   WHERE consumer_id=? AND stream=?""",
                (seq, status, consumer_id, stream),
            )
            conn.commit()
            return _ack_body(
                consumer_id, stream, acked=seq, head=head, status=status, duplicate=False,
            )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def health(self) -> dict[str, Any]:
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            ensure_schema(conn)
            heads = {stream: _head(conn, stream) for stream in STREAMS}
            rows = conn.execute(
                """SELECT consumer_id, stream, acked_seq, status, dropped
                   FROM paper_event_consumer ORDER BY consumer_id, stream"""
            ).fetchall()
        finally:
            conn.close()
        with _LIVE_LOCK:
            live = {
                key: (len(queue.items), queue.resync, queue.dropped)
                for key, queue in _LIVE.items()
                if key[0] == self.path
            }
        consumers = []
        for row in rows:
            key = (self.path, row["consumer_id"], row["stream"])
            pending, live_resync, live_dropped = live.get(key, (0, False, 0))
            head = heads.get(row["stream"], 0)
            acked = int(row["acked_seq"])
            status = "resync_required" if live_resync else row["status"]
            consumers.append({
                "consumer_id": row["consumer_id"],
                "stream": row["stream"],
                "acked_seq": acked,
                "head_seq": head,
                "lag": head - acked,
                "status": status,
                "dropped": int(row["dropped"]) + live_dropped,
                "pending_in_memory": pending,
            })
        return {
            "schema": SCHEMA,
            "available": True,
            "live_execution": False,
            "live_trading_authorized": False,
            "broker_order_submitted": False,
            "max_pending": self.max_pending,
            "live_dispatch_failures": _dispatch_failures,
            "streams": [{"stream": stream, "head_seq": heads[stream]} for stream in STREAMS],
            "consumers": consumers,
        }

    def forget_process_memory(self) -> None:
        """Drop in-memory queues for this file. Durable rows and acks stay."""
        with _LIVE_LOCK:
            for key in [key for key in _LIVE if key[0] == self.path]:
                del _LIVE[key]

    def _touch_consumer(self, consumer_id: str, stream: str, *, status: str) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            ensure_schema(conn)
            _ensure_consumer(conn, consumer_id, stream)
            if status == "active":
                current = _consumer_row(conn, consumer_id, stream)
                if current is not None and current["status"] == "disconnected":
                    conn.execute(
                        """UPDATE paper_event_consumer SET status='active'
                           WHERE consumer_id=? AND stream=?""",
                        (consumer_id, stream),
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _set_status(self, consumer_id: str, stream: str, status: str) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            ensure_schema(conn)
            _ensure_consumer(conn, consumer_id, stream)
            conn.execute(
                """UPDATE paper_event_consumer SET status=?
                   WHERE consumer_id=? AND stream=?""",
                (status, consumer_id, stream),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _consumer_exists(self, consumer_id: str, stream: str) -> bool:
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            return _consumer_row(conn, consumer_id, stream) is not None
        finally:
            conn.close()

    def _consumer_view(self, consumer_id: str, stream: str) -> dict[str, Any]:
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            row = _consumer_row(conn, consumer_id, stream)
            head = _head(conn, stream)
        finally:
            conn.close()
        acked = 0 if row is None else int(row["acked_seq"])
        return {
            "consumer_id": consumer_id,
            "stream": stream,
            "acked_seq": acked,
            "head_seq": head,
            "lag": head - acked,
            "status": None if row is None else row["status"],
        }


def _head(conn: sqlite3.Connection, stream: str) -> int:
    row = conn.execute(
        "SELECT seq FROM paper_event_log WHERE stream=? ORDER BY seq DESC LIMIT 1",
        (stream,),
    ).fetchone()
    return 0 if row is None else int(row["seq"])


def _after(conn: sqlite3.Connection, stream: str, seq: int, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM paper_event_log
           WHERE stream=? AND seq>? ORDER BY seq LIMIT ?""",
        (stream, seq, limit),
    ).fetchall()
    return [_public_event(row) for row in rows]


def _ensure_consumer(conn: sqlite3.Connection, consumer_id: str, stream: str) -> None:
    conn.execute(
        """INSERT INTO paper_event_consumer(consumer_id, stream, acked_seq, status, dropped)
           VALUES (?,?,0,'active',0)
           ON CONFLICT(consumer_id, stream) DO NOTHING""",
        (consumer_id, stream),
    )


def _consumer_row(conn: sqlite3.Connection, consumer_id: str, stream: str) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT consumer_id, stream, acked_seq, status, dropped
           FROM paper_event_consumer WHERE consumer_id=? AND stream=?""",
        (consumer_id, stream),
    ).fetchone()


def _page(
    *,
    stream: str,
    consumer_id: str,
    status: str,
    signal: str | None,
    events: list[dict[str, Any]],
    gaps: list[dict[str, int]],
    acked: int,
    head: int,
    dropped: int,
    replay_from: int,
) -> dict[str, Any]:
    next_from = events[-1]["seq"] if events else replay_from
    return {
        "schema": SCHEMA,
        "stream": stream,
        "consumer_id": consumer_id,
        "status": status,
        "signal": signal,
        "events": events,
        "gaps": gaps,
        "acked_seq": acked,
        "head_seq": head,
        "lag": head - acked,
        "dropped": dropped,
        "replay_from": replay_from,
        "next_from": next_from,
        "retained": True,
        "live_execution": False,
        "live_trading_authorized": False,
        "broker_order_submitted": False,
    }


def _ack_body(
    consumer_id: str, stream: str, *, acked: int, head: int, status: str, duplicate: bool,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "consumer_id": consumer_id,
        "stream": stream,
        "acked_seq": acked,
        "head_seq": head,
        "lag": head - acked,
        "status": status,
        "duplicate": duplicate,
        "live_execution": False,
        "live_trading_authorized": False,
        "broker_order_submitted": False,
    }
