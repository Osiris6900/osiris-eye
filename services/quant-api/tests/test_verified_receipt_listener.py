"""Protected receipt socket integration tests."""
import json
import os
import socket
import struct
import threading
import time

import pytest

from osiris_api.recipient_receipt_ledger import RecipientReceiptLedger
from osiris_api.verified_receipt_consumer import VerifiedReceiptConsumer
from osiris_api.verified_receipt_listener import serve_one_receipt


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="Linux only")
def test_one_shot_socket_commits(tmp_path):
    ledger = RecipientReceiptLedger(str(tmp_path / "ledger.db"))
    ledger.register("message-1", "peer-2", "a" * 64, now=999)
    consumer = VerifiedReceiptConsumer(ledger, sender_peer_id="peer-1")
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    sock_path = str(parent / "receipt.sock")
    outcome = []

    def worker():
        try:
            outcome.append(serve_one_receipt(
                sock_path, consumer, expected_rust_pid=os.getpid(),
                expected_rust_uid=os.getuid(), now=1000,
                timeout_seconds=3,
            ))
        except Exception as exc:
            outcome.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        for _ in range(100):
            if os.path.exists(sock_path):
                break
            time.sleep(0.01)
        payload = json.dumps({
            "schema": "osiris.p2p.verified-receipt-event.v1",
            "event_id": "evt-1", "sender_peer_id": "peer-1",
            "recipient_peer_id": "peer-2", "message_id": "message-1",
            "envelope_sha256": "a" * 64, "accepted_at": 1000,
        }).encode()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(sock_path)
            client.sendall(struct.pack("!I", len(payload)) + payload)
    finally:
        thread.join(timeout=5)
    assert outcome == [True]
    assert ledger.status("message-1", "peer-2", "a" * 64) == "acknowledged"
    assert not os.path.exists(sock_path)


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="Linux only")
def test_rejects_world_writable_directory(tmp_path):
    directory = tmp_path / "insecure"
    directory.mkdir()
    directory.chmod(0o777)
    ledger = RecipientReceiptLedger(str(tmp_path / "ledger.db"))
    consumer = VerifiedReceiptConsumer(ledger, sender_peer_id="peer-1")
    with pytest.raises(ValueError, match="insecure_receipt_socket_directory"):
        serve_one_receipt(str(directory / "receipt.sock"), consumer,
                          expected_rust_pid=os.getpid(),
                          expected_rust_uid=os.getuid(), now=1000)
