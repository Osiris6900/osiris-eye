#!/usr/bin/env python3
"""Cross-process receipt bridge: real Rust verifier, Python SQLite ledger.

The Rust binary calls PendingReceipts::verify. It never emits a trusted event
for a failed signature, peer, digest, or admission check. Python acknowledges a
row only after that event is committed, and only for the pinned Rust PID.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "quant-api" / "src"))

from osiris_api.recipient_receipt_ledger import RecipientReceiptLedger
from osiris_api.verified_receipt_consumer import VerifiedReceiptConsumer
from osiris_api.verified_receipt_unix import (
    accept_one_verified_receipt,
    bind_verified_receipt_listener,
)

MANIFEST = ROOT / "services" / "p2p-node" / "Cargo.toml"
BINARY_NAME = "verified-receipt-bridge"


class Failure(Exception):
    pass


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise Failure(message)


def build_binary() -> Path:
    subprocess.run(
        ["cargo", "build", "--locked", "--manifest-path", str(MANIFEST), "--bin", BINARY_NAME],
        check=True,
    )
    binary = ROOT / "services" / "p2p-node" / "target" / "debug" / BINARY_NAME
    expect(binary.is_file(), f"missing binary {binary}")
    return binary


def run_json(binary: Path, args: list[str], *, timeout: float = 30) -> dict:
    completed = subprocess.run(
        [str(binary), *args], check=False, capture_output=True, text=True, timeout=timeout,
    )
    line = next((item for item in reversed(completed.stdout.splitlines()) if item.startswith("{")), "")
    try:
        report = json.loads(line) if line else {}
    except json.JSONDecodeError as exc:
        raise Failure(f"unreadable bridge output: {completed.stdout!r} {completed.stderr!r}") from exc
    report["_exit"] = completed.returncode
    report["_stderr"] = completed.stderr
    return report


def private_dir(parent: Path, name: str) -> Path:
    path = parent / name
    path.mkdir(mode=0o700)
    os.chmod(path, 0o700)
    return path


def bridge_args(directory: Path, scenario: str) -> list[str]:
    return [
        "--scenario", scenario,
        "--socket", str(directory / "receipt.sock"),
        "--spool", str(directory / "spool.sqlite3"),
        "--listener-pid", str(os.getpid()),
    ]


def correlated(report: dict) -> tuple[str, str, str]:
    return report["message_id"], report["recipient_peer_id"], report["envelope_sha256"]


def fresh_ledger(directory: Path, report: dict) -> tuple[RecipientReceiptLedger, VerifiedReceiptConsumer]:
    ledger = RecipientReceiptLedger(str(directory / "ledger.sqlite3"))
    message_id, recipient, digest = correlated(report)
    ledger.register(message_id, recipient, digest, now=int(time.time()))
    consumer = VerifiedReceiptConsumer(ledger, sender_peer_id=report["sender_peer_id"])
    return ledger, consumer


def accept(listener: socket.socket, consumer: VerifiedReceiptConsumer, rust_pid: int):
    try:
        return accept_one_verified_receipt(
            listener, consumer,
            expected_rust_pid=rust_pid,
            expected_rust_uid=os.getuid(),
            now=int(time.time()),
        )
    except (OSError, ValueError, RuntimeError) as exc:
        return exc


def exchange(binary: Path, directory: Path, scenario: str, consumer: VerifiedReceiptConsumer, *, accepts: int = 1):
    listener = bind_verified_receipt_listener(str(directory / "receipt.sock"))
    listener.settimeout(8)
    proc = subprocess.Popen(
        [str(binary), *bridge_args(directory, scenario)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    outcomes = [accept(listener, consumer, proc.pid) for _ in range(accepts)]
    stdout, stderr = proc.communicate(timeout=20)
    listener.close()
    line = next((item for item in reversed(stdout.splitlines()) if item.startswith("{")), "")
    report = json.loads(line) if line else {}
    report["_exit"] = proc.returncode
    report["_stderr"] = stderr
    report["_outcomes"] = outcomes
    return report


def assert_rejected(binary: Path, root: Path, identity: dict, scenario: str, reason: str) -> None:
    directory = private_dir(root, scenario)
    ledger, _consumer = fresh_ledger(directory, identity)
    listener = bind_verified_receipt_listener(str(directory / "receipt.sock"))
    listener.settimeout(0.4)
    report = run_json(binary, bridge_args(directory, scenario))
    try:
        connection, _address = listener.accept()
        connection.close()
        raise Failure(f"{scenario} connected to the listener")
    except TimeoutError:
        pass
    finally:
        listener.close()
    expect(report["_exit"] == 0, f"{scenario} exit {report}")
    expect(report["result"] == "rejected", str(report))
    expect(report["reason"] == reason, str(report))
    expect(report["unacked"] == 0, str(report))
    message_id, recipient, digest = correlated(identity)
    expect(ledger.status(message_id, recipient, digest) == "pending", ledger.counts())


def main() -> None:
    if not hasattr(socket, "SO_PEERCRED"):
        raise Failure("linux_peer_credentials_required")
    binary = build_binary()
    identity = run_json(binary, ["--scenario", "print"])
    expect(identity["_exit"] == 0 and identity["result"] == "printed", str(identity))
    expect(identity["sender_peer_id"] != identity["recipient_peer_id"], str(identity))

    with tempfile.TemporaryDirectory(prefix="osiris-receipt-bridge-") as temporary:
        root = Path(temporary)
        os.chmod(root, 0o700)

        for scenario, reason in (
            ("bad-signature", "receipt_signature_invalid"),
            ("wrong-peer", "receipt_publisher_identity_mismatch"),
            ("wrong-digest", "receipt_envelope_digest_mismatch"),
            ("policy-denied", "receipt_admission_policy_rejected"),
        ):
            assert_rejected(binary, root, identity, scenario, reason)

        valid_dir = private_dir(root, "valid")
        ledger, consumer = fresh_ledger(valid_dir, identity)
        valid = exchange(binary, valid_dir, "valid", consumer)
        message_id, recipient, digest = correlated(identity)
        expect(valid["_exit"] == 0 and valid["result"] == "applied", str(valid))
        expect(valid["_outcomes"] == ["applied"], str(valid))
        expect(valid["unacked"] == 0, str(valid))
        expect(ledger.status(message_id, recipient, digest) == "acknowledged", ledger.counts())
        reopened = RecipientReceiptLedger(str(valid_dir / "ledger.sqlite3"))
        expect(reopened.counts()["acknowledged"] == 1, reopened.counts())
        expect(reopened.counts()["pending"] == 0 and reopened.counts()["unconfirmed"] == 0, reopened.counts())

        replay_dir = private_dir(root, "replay")
        replay_ledger, replay_consumer = fresh_ledger(replay_dir, identity)
        replay = exchange(binary, replay_dir, "replay", replay_consumer, accepts=2)
        expect(replay["_exit"] == 0 and replay["result"] == "replayed", str(replay))
        expect(replay["disposition"] == "applied", str(replay))
        expect(replay["second_disposition"] == "duplicate", str(replay))
        expect(replay["_outcomes"] == ["applied", "duplicate"], str(replay))
        expect(replay_ledger.counts()["acknowledged"] == 1, replay_ledger.counts())
        expect(replay["reason"] == "receipt_no_pending_attempt", str(replay))

        restart_dir = private_dir(root, "restart")
        restart_ledger, restart_consumer = fresh_ledger(restart_dir, identity)
        prepared = run_json(binary, bridge_args(restart_dir, "prepare"))
        expect(prepared["_exit"] == 0 and prepared["result"] == "spooled", str(prepared))
        expect(prepared["unacked"] == 1, str(prepared))
        expect(restart_ledger.status(message_id, recipient, digest) == "pending", restart_ledger.counts())
        flushed = exchange(binary, restart_dir, "flush", restart_consumer)
        expect(flushed["_exit"] == 0 and flushed["disposition"] == "applied", str(flushed))
        expect(flushed["unacked"] == 0, str(flushed))
        expect(restart_ledger.status(message_id, recipient, digest) == "acknowledged", restart_ledger.counts())
        idle = run_json(binary, bridge_args(restart_dir, "flush"))
        expect(idle["result"] == "idle" and idle["unacked"] == 0, str(idle))
        expect(restart_ledger.counts()["acknowledged"] == 1, restart_ledger.counts())

        outage_dir = private_dir(root, "outage")
        outage_ledger, outage_consumer = fresh_ledger(outage_dir, identity)
        deferred = run_json(binary, bridge_args(outage_dir, "channel-down"))
        expect(deferred["_exit"] == 0 and deferred["result"] == "deferred", str(deferred))
        expect(deferred["unacked"] == 1, str(deferred))
        expect(outage_ledger.status(message_id, recipient, digest) == "pending", outage_ledger.counts())
        recovered = exchange(binary, outage_dir, "flush", outage_consumer)
        expect(recovered["disposition"] == "applied" and recovered["unacked"] == 0, str(recovered))
        expect(outage_ledger.status(message_id, recipient, digest) == "acknowledged", outage_ledger.counts())

        order_dir = private_dir(root, "out-of-order")
        unordered = RecipientReceiptLedger(str(order_dir / "ledger.sqlite3"))
        unordered_consumer = VerifiedReceiptConsumer(unordered, sender_peer_id=identity["sender_peer_id"])
        early = exchange(binary, order_dir, "valid", unordered_consumer)
        expect(early["_exit"] == 0 and early["result"] == "retained", str(early))
        expect(early["unacked"] == 1, str(early))
        expect(isinstance(early["_outcomes"][0], ValueError), str(early))
        expect("receipt_event_not_pending" in str(early["_outcomes"][0]), str(early))
        expect(unordered.status(message_id, recipient, digest) is None, unordered.counts())
        unordered.register(message_id, recipient, digest, now=int(time.time()))
        ordered = exchange(binary, order_dir, "flush", unordered_consumer)
        expect(ordered["disposition"] == "applied", str(ordered))
        expect(unordered.status(message_id, recipient, digest) == "acknowledged", unordered.counts())

        malicious_dir = private_dir(root, "malicious")
        malicious_ledger, malicious_consumer = fresh_ledger(malicious_dir, identity)
        malicious_listener = bind_verified_receipt_listener(str(malicious_dir / "receipt.sock"))
        box: dict = {}

        def serve_malicious() -> None:
            try:
                box["value"] = accept_one_verified_receipt(
                    malicious_listener, malicious_consumer,
                    expected_rust_pid=os.getpid(),
                    expected_rust_uid=os.getuid(),
                    now=int(time.time()),
                )
            except (OSError, ValueError, RuntimeError) as exc:
                box["error"] = exc

        thread = threading.Thread(target=serve_malicious)
        thread.start()
        injected = subprocess.run(
            [
                sys.executable, "-c",
                (
                    "import json,socket,struct,sys; body=json.dumps({'verified': True, "
                    "'schema': 'osiris.p2p.verified-receipt-event.v1'}).encode(); "
                    "s=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect(sys.argv[1]); "
                    "s.sendall(struct.pack('!I', len(body))+body); s.settimeout(1); "
                    "sys.stdout.buffer.write(s.recv(16))"
                ),
                str(malicious_dir / "receipt.sock"),
            ],
            check=False, capture_output=True, timeout=5,
        )
        thread.join(5)
        malicious_listener.close()
        expect("unauthenticated_rust_peer" in str(box.get("error")), str(box))
        expect(injected.stdout == b"", injected.stdout)
        expect(malicious_ledger.status(message_id, recipient, digest) == "pending", malicious_ledger.counts())
        with pytest_direct_flag(malicious_ledger, message_id, recipient, digest):
            pass

    print("PASS: Rust verifier and Python SQLite ledger bridged fail-closed", flush=True)


def pytest_direct_flag(ledger: RecipientReceiptLedger, message_id: str, recipient: str, digest: str):
    class _Guard:
        def __enter__(self):
            try:
                ledger.acknowledge_verified(message_id, recipient, digest, verified=True)
            except ValueError as exc:
                expect(str(exc) == "untrusted_verification_flag", str(exc))
                return self
            raise Failure("verified=True acknowledged a receipt")

        def __exit__(self, *_args):
            return False

    return _Guard()


if __name__ == "__main__":
    try:
        main()
    except (Failure, subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
