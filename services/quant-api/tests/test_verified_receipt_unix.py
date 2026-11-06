"""Kernel credential, framing and replay tests for trusted Unix receipt adapter."""
import json
import os
import socket
import struct

import pytest

from osiris_api.recipient_receipt_ledger import RecipientReceiptLedger
from osiris_api.verified_receipt_consumer import VerifiedReceiptConsumer
from osiris_api.verified_receipt_unix import receive_verified_receipt, MAX_FRAME_BYTES


def event():
    return {
        "schema": "osiris.p2p.verified-receipt-event.v1",
        "event_id": "event-1",
        "sender_peer_id": "peer-1",
        "recipient_peer_id": "peer-2",
        "message_id": "message-1",
        "envelope_sha256": "a" * 64,
        "accepted_at": 1000,
    }


def consumer(tmp_path):
    ledger = RecipientReceiptLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.register("message-1", "peer-2", "a" * 64, now=999)
    return ledger, VerifiedReceiptConsumer(ledger, sender_peer_id="peer-1")


def send_frame(sock, data):
    sock.sendall(struct.pack("!I", len(data)) + data)


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="Linux only")
def test_pinned_peer_and_idempotent_restart(tmp_path):
    ledger, handler = consumer(tmp_path)
    for expected in (True, False):
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            send_frame(left, json.dumps(event()).encode())
            assert receive_verified_receipt(right, handler, expected_rust_pid=os.getpid(),
                                            expected_rust_uid=os.getuid(), now=1000) is expected
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
            receive_verified_receipt(right, handler, expected_rust_pid=os.getpid() + 1,
                                     expected_rust_uid=os.getuid(), now=1000)
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
            receive_verified_receipt(right, handler, expected_rust_pid=os.getpid(),
                                     expected_rust_uid=os.getuid(), now=1000)
    finally:
        left.close()
        right.close()
    assert ledger.counts()["pending"] == 1
