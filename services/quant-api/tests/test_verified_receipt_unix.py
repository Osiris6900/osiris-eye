"""Kernel credential, framing, durability and malicious-caller tests."""
import json
import os
import socket
import sqlite3
import struct
import subprocess
import sys
import threading

import pytest

from osiris_api.recipient_receipt_ledger import RecipientReceiptLedger
from osiris_api.verified_receipt_consumer import VerifiedReceiptConsumer
from osiris_api.verified_receipt_event import VERIFIED_EVENT_PROVENANCE, VERIFIED_EVENT_SCHEMA
from osiris_api.verified_receipt_unix import (
    MAX_FRAME_BYTES,
    accept_one_verified_receipt,
    bind_verified_receipt_listener,
    receive_verified_receipt,
)


def event(**overrides):
    body = {
        "schema": VERIFIED_EVENT_SCHEMA,
        "event_id": "event-1",
        "sender_peer_id": "peer-1",
        "recipient_peer_id": "peer-2",
        "message_id": "message-1",
        "envelope_sha256": "a" * 64,
        "accepted_at": 1000,
        "provenance": VERIFIED_EVENT_PROVENANCE,
    }
    body.update(overrides)
    return body


def consumer(tmp_path):
    ledger = RecipientReceiptLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.register("message-1", "peer-2", "a" * 64, now=999)
    return ledger, VerifiedReceiptConsumer(ledger, sender_peer_id="peer-1")


def send_frame(sock, data):
    sock.sendall(struct.pack("!I", len(data)) + data)


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="Linux only")
def test_pinned_peer_and_idempotent_restart(tmp_path):
    ledger, handler = consumer(tmp_path)
    for expected in ("applied", "duplicate"):
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            send_frame(left, json.dumps(event()).encode())
            assert receive_verified_receipt(
                right, handler, expected_rust_pid=os.getpid(), expected_rust_uid=os.getuid(), now=1000,
            ) == expected
        finally:
            left.close()
            right.close()
    assert ledger.counts()["acknowledged"] == 1


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="Linux only")
def test_wrong_pid_rejected_before_read(tmp_path):
    ledger, handler = consumer(tmp_path)
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        with pytest.raises(ValueError, match="unauthenticated_rust_peer"):
            receive_verified_receipt(
                right, handler, expected_rust_pid=os.getpid() + 1, expected_rust_uid=os.getuid(), now=1000,
            )
    finally:
        left.close()
        right.close()
    assert ledger.counts()["pending"] == 1


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="Linux only")
@pytest.mark.parametrize("data", [
    b'{"schema":"x","schema":"y"}',
    b'not-json',
    b'',
    b'x' * (MAX_FRAME_BYTES + 1),
])
def test_bad_frames_fail_closed(tmp_path, data):
    ledger, handler = consumer(tmp_path)
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        send_frame(left, data)
        with pytest.raises(ValueError):
            receive_verified_receipt(
                right, handler, expected_rust_pid=os.getpid(), expected_rust_uid=os.getuid(), now=1000,
            )
    finally:
        left.close()
        right.close()
    assert ledger.counts()["pending"] == 1


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="Linux only")
def test_authenticated_verified_flag_is_not_proof(tmp_path):
    ledger, handler = consumer(tmp_path)
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        forged = event()
        forged["verified"] = True
        send_frame(left, json.dumps(forged).encode())
        with pytest.raises(ValueError, match="invalid_receipt_event_fields"):
            receive_verified_receipt(
                right, handler, expected_rust_pid=os.getpid(), expected_rust_uid=os.getuid(), now=1000,
            )
        left.settimeout(0.2)
        with pytest.raises(TimeoutError):
            left.recv(1)
    finally:
        left.close()
        right.close()
    assert ledger.status("message-1", "peer-2", "a" * 64) == "pending"


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="Linux only")
def test_interrupted_insert_rolls_back_and_writes_no_ack(tmp_path):
    ledger, handler = consumer(tmp_path)
    with ledger._connect() as db:
        db.execute(
            """CREATE TRIGGER abort_receipt_event BEFORE INSERT ON verified_receipt_events
               BEGIN SELECT RAISE(ABORT, 'interrupted_write'); END;"""
        )
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        send_frame(left, json.dumps(event()).encode())
        with pytest.raises(sqlite3.Error, match="interrupted_write"):
            receive_verified_receipt(
                right, handler, expected_rust_pid=os.getpid(), expected_rust_uid=os.getuid(), now=1000,
            )
        left.settimeout(0.2)
        with pytest.raises(TimeoutError):
            left.recv(1)
    finally:
        left.close()
        right.close()
    assert ledger.status("message-1", "peer-2", "a" * 64) == "pending"
    with ledger._connect() as db:
        db.execute("DROP TRIGGER abort_receipt_event")
        assert db.execute("SELECT COUNT(*) FROM verified_receipt_events").fetchone()[0] == 0
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        send_frame(left, json.dumps(event()).encode())
        assert receive_verified_receipt(
            right, handler, expected_rust_pid=os.getpid(), expected_rust_uid=os.getuid(), now=1000,
        ) == "applied"
    finally:
        left.close()
        right.close()
    assert ledger.status("message-1", "peer-2", "a" * 64) == "acknowledged"


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="Linux only")
def test_lost_ack_after_commit_replays_as_duplicate(tmp_path, monkeypatch):
    ledger, handler = consumer(tmp_path)
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)

    def explode(_connection, _payload):
        raise RuntimeError("ack_transport_lost")

    monkeypatch.setattr("osiris_api.verified_receipt_unix._write_frame", explode)
    try:
        send_frame(left, json.dumps(event()).encode())
        with pytest.raises(RuntimeError, match="ack_transport_lost"):
            receive_verified_receipt(
                right, handler, expected_rust_pid=os.getpid(), expected_rust_uid=os.getuid(), now=1000,
            )
    finally:
        left.close()
        right.close()
    assert ledger.status("message-1", "peer-2", "a" * 64) == "acknowledged"
    monkeypatch.undo()
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        send_frame(left, json.dumps(event()).encode())
        assert receive_verified_receipt(
            right, handler, expected_rust_pid=os.getpid(), expected_rust_uid=os.getuid(), now=1001,
        ) == "duplicate"
    finally:
        left.close()
        right.close()
    assert ledger.counts()["acknowledged"] == 1


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="Linux only")
def test_other_local_process_cannot_inject_a_receipt(tmp_path):
    ledger, handler = consumer(tmp_path)
    listener = bind_verified_receipt_listener(str(tmp_path / "private" / "receipt.sock"))
    outcome = {}

    def serve():
        try:
            outcome["value"] = accept_one_verified_receipt(
                listener, handler, expected_rust_pid=os.getpid(),
                expected_rust_uid=os.getuid(), now=1000,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=serve)
    thread.start()
    script = """
import json, socket, struct, sys
body = json.dumps({
    "schema": "osiris.p2p.verified-receipt-event.v1",
    "event_id": "event-1",
    "sender_peer_id": "peer-1",
    "recipient_peer_id": "peer-2",
    "message_id": "message-1",
    "envelope_sha256": "%s",
    "accepted_at": 1000,
    "provenance": "rust.delivery_receipt.accept.v1",
    "verified": True,
}).encode()
sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(sys.argv[1])
sock.sendall(struct.pack("!I", len(body)) + body)
sock.settimeout(1)
sys.stdout.buffer.write(sock.recv(32))
""" % ("a" * 64)
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "private" / "receipt.sock")],
        check=False, capture_output=True, timeout=5,
    )
    thread.join(5)
    listener.close()
    assert thread.is_alive() is False
    assert "unauthenticated_rust_peer" in str(outcome.get("error"))
    assert completed.stdout == b""
    assert ledger.counts()["pending"] == 1


def test_listener_rejects_relative_symlink_and_occupied_paths(tmp_path):
    with pytest.raises(ValueError, match="invalid_receipt_socket_path"):
        bind_verified_receipt_listener("relative.sock")
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="receipt_socket_directory_invalid"):
        bind_verified_receipt_listener(str(link / "receipt.sock"))
    occupied = tmp_path / "occupied-dir"
    occupied.mkdir()
    (occupied / "receipt.sock").write_text("not-a-socket", encoding="utf-8")
    with pytest.raises(ValueError, match="receipt_socket_path_occupied"):
        bind_verified_receipt_listener(str(occupied / "receipt.sock"))


def test_http_routes_cannot_acknowledge_receipts():
    from osiris_api.p2p_routes import router
    paths = {getattr(route, "path", "") for route in router.routes}
    assert not any("receipt" in path for path in paths)
    root = __import__("pathlib").Path(__file__).resolve().parents[1] / "src" / "osiris_api"
    for name in ("p2p_routes.py", "main.py", "observation_outbox.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "acknowledge_verified" not in text
        assert "RecipientReceiptLedger" not in text
        assert "verified_receipt" not in text
