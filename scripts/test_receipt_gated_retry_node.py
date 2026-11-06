#!/usr/bin/env python3
"""Process test: a tracked Rust envelope is not written to stdin again.

A second stdin write mints a new message id. The scheduler must not do that
after receipts_tracked=true, and it must not write when the receipt channel
is unavailable or the recipient is revoked.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "quant-api" / "src"))

from osiris_api.receipt_gated_retry import (
    AdmissionDecision,
    ReceiptRetryScheduler,
)
from osiris_api.recipient_receipt_ledger import RecipientReceiptLedger

HARNESS_PATH = ROOT / "scripts" / "test_p2p_delivery_receipts.py"


def load_harness():
    spec = importlib.util.spec_from_file_location("delivery_receipt_harness", HARNESS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def observation(payload: str) -> dict:
    return {
        "schema": "osiris.observation.v1",
        "instrument": "P2P-RETRY-GATE",
        "observed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provider": "synthetic-delivery-receipts",
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
    }


def main() -> None:
    harness = load_harness()
    matches = [
        path for path in (
            ROOT / "services" / "p2p-node" / "target" / "release" / "osiris-p2p-node",
            ROOT / "services" / "p2p-node" / "target" / "debug" / "osiris-p2p-node",
        ) if path.is_file()
    ]
    if not matches:
        raise RuntimeError("osiris-p2p-node binary is not built")
    binary = matches[0]
    os.environ["OSIRIS_P2P_INGEST_ENABLED"] = "1"
    os.environ["OSIRIS_OUTBOX_RECEIPT_RETRY"] = "1"
    with tempfile.TemporaryDirectory(prefix="osiris-receipt-retry-") as temporary:
        directory = Path(temporary)
        nodes = harness.Nodes(binary, directory)
        try:
            recipients = directory / "recipients.txt"
            recipients.write_text("", encoding="utf-8")
            sender = nodes.start("node-a", recipients_file=recipients)
            listener = nodes.wait_for(
                lambda name, line: name == "node-a" and line.startswith("listener_ready "),
                "sender listener", timeout=20,
            )
            address = harness.fields(listener)["address"]
            nodes.start("node-b", bootstrap=address)
            peer_line = nodes.wait_for(
                lambda name, line: name == "node-b" and line.startswith("peer_id="),
                "recipient identity", timeout=20,
            )
            recipient = harness.fields(peer_line)["peer_id"]
            nodes.wait_for(
                lambda name, line: name == "node-a"
                and line.startswith(f"key_binding_accepted peer={recipient} "),
                "sender accepted recipient key binding", timeout=30,
            )
            nodes.capture_for(2)
            recipients.write_text(recipient + "\n", encoding="utf-8")
            ledger = RecipientReceiptLedger(str(directory / "ledger.sqlite3"))
            scheduler = ReceiptRetryScheduler(str(directory / "retry.sqlite3"), ledger)
            payload = observation("retry-gate probe")
            now = int(datetime.now(UTC).timestamp())
            assert scheduler.schedule(payload, recipient, now=now)
            writes = []

            def publish(line: str, peer: str) -> dict:
                writes.append((line, peer))
                sender.stdin.write(line if line.endswith("\n") else line + "\n")
                sender.stdin.flush()
                published = harness.fields(nodes.wait_for(
                    lambda name, output: name == "node-a"
                    and output.startswith(f"encrypted_observation_published recipient={peer} "),
                    "tracked publication", timeout=20,
                ))
                if published.get("receipts_tracked") != "true":
                    return {"status": "deferred"}
                return {
                    "status": "tracked",
                    "message_id": published["message_id"],
                    "envelope_sha256": published["envelope_sha256"],
                }

            allowed = {"synthetic-delivery-receipts"}
            admitted = AdmissionDecision({recipient}, set())
            first = scheduler.advance(
                publish=publish, allowed_providers=allowed, policy=admitted, now=now,
            )
            assert first[0]["state"] == "published", first
            assert len(writes) == 1
            message_id = scheduler.snapshot(
                ":".join((payload["provider"], payload["instrument"], payload["payload_sha256"])),
                recipient,
            )["message_id"]
            digest = scheduler.snapshot(
                ":".join((payload["provider"], payload["instrument"], payload["payload_sha256"])),
                recipient,
            )["envelope_sha256"]
            assert ledger.status(message_id, recipient, digest) == "pending"
            scheduler.advance(
                publish=publish, allowed_providers=allowed, policy=admitted, now=now + 1,
            )
            assert len(writes) == 1, "scheduler reminted an envelope"

            direct = observation("manual second stdin write")
            sender.stdin.write(json.dumps(direct) + "\n")
            sender.stdin.flush()
            again = nodes.wait_for(
                lambda name, output: name == "node-a"
                and output.startswith(f"encrypted_observation_published recipient={recipient} ")
                and harness.fields(output).get("message_id") != message_id,
                "node mints a different message id for a second stdin write", timeout=20,
            )
            assert harness.fields(again)["message_id"] != message_id

            blocked = observation("revoked recipient")
            assert scheduler.schedule(blocked, recipient, now=now + 2)
            before = len(writes)
            denied = scheduler.advance(
                publish=publish, allowed_providers=allowed,
                policy=AdmissionDecision({recipient}, {recipient}), now=now + 2,
            )
            assert denied[0]["reason"] == "recipient_revoked"
            assert len(writes) == before

            quiet = observation("channel down")
            assert scheduler.schedule(quiet, recipient, now=now + 3)
            deferred = scheduler.advance(
                publish=publish, allowed_providers=allowed, policy=admitted,
                now=now + 3, channel_ready=False,
            )
            assert deferred[0]["reason"] == "receipt_channel_unavailable"
            assert len(writes) == before
            print(
                "PASS: tracked envelope was not rewritten; revoked and "
                "channel-down attempts did not write stdin",
                flush=True,
            )
        finally:
            nodes.close()


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, OSError, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
