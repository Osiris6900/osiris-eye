#!/usr/bin/env python3
"""Exercise recipient E2EE through Circuit Relay v2 using the real core node.

Topology: sender core node -> relay server -> recipient core node.  The sender
bootstraps only the recipient's circuit address and mDNS and automatic hole
punching are disabled on both nodes. The test asserts that the observed sender/recipient connection is
relayed, signed encryption-key bindings are accepted durably, and the recipient
reports the exact decrypted observation digest.  It uses fresh state and
ephemeral localhost ports; it does not establish cross-host/NAT reachability.

Build all binaries first: cargo build --release --manifest-path
services/p2p-node/Cargo.toml --bins
"""

from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def find_binaries(binary_dir: Path | None) -> tuple[Path, Path]:
    candidates = [binary_dir] if binary_dir else [
        ROOT / "target" / "release",
        ROOT / "services" / "p2p-node" / "target" / "release",
    ]
    for directory in candidates:
        node, relay = directory / "osiris-p2p-node", directory / "relay"
        if node.is_file() and relay.is_file():
            return node.resolve(), relay.resolve()
    raise FileNotFoundError(
        "core node and relay binaries missing; build with cargo build --release "
        "--manifest-path services/p2p-node/Cargo.toml --bins"
    )


def binary_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_revision() -> dict:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=ROOT, check=True, capture_output=True, text=True, timeout=5,
        ).stdout
        return {"source_revision": revision, "source_dirty": bool(status)}
    except (OSError, subprocess.SubprocessError):
        return {"source_revision": None, "source_dirty": None}


class Process:
    """Bounded log collection for one directly launched OS process."""

    def __init__(self, name: str, binary: Path, env: dict[str, str]):
        self.name = name
        self.proc = subprocess.Popen(
            [str(binary)], cwd=ROOT, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", bufsize=1,
        )
        self.history: deque[str] = deque(maxlen=4000)
        self.events: queue.Queue[str] = queue.Queue(maxsize=4000)
        self.lock = threading.Lock()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            line = raw.rstrip()
            with self.lock:
                self.history.append(line)
            print(f"[{self.name}] {line}", flush=True)
            try:
                self.events.put_nowait(line)
            except queue.Full:
                # History remains bounded and a noisy peer cannot block the
                # pipe reader or grow the controller's memory without limit.
                pass

    def logs(self) -> list[str]:
        with self.lock:
            return list(self.history)

    def wait_for(self, predicate, timeout: float, description: str) -> str:
        deadline = time.monotonic() + timeout
        while True:
            for line in self.logs():
                if predicate(line):
                    return line
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"{self.name} exited {self.proc.returncode} before {description}"
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{self.name}: timed out waiting for {description}")
            try:
                line = self.events.get(timeout=min(remaining, 0.25))
                if predicate(line):
                    return line
            except queue.Empty:
                pass

    def prefix(self, prefix: str, timeout: float) -> str:
        return self.wait_for(lambda line: line.startswith(prefix), timeout, prefix)


def stop_all(processes: list[Process]) -> None:
    # Terminate all first, then reap against one shared deadline.
    for process in processes:
        if process.proc.poll() is None:
            process.proc.terminate()
    deadline = time.monotonic() + 5
    for process in processes:
        try:
            process.proc.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.proc.kill()
            process.proc.wait(timeout=5)
        if process.proc.stdin is not None:
            process.proc.stdin.close()
        process.reader.join(timeout=1)
        if process.proc.stdout is not None:
            process.proc.stdout.close()


def core_environment(base: dict[str, str], tmp: Path, name: str) -> dict[str, str]:
    return base | {
        "OSIRIS_NODE_KEY": str(tmp / f"{name}.identity"),
        "OSIRIS_TRANSPORT_KEY": str(tmp / f"{name}.transport"),
        "OSIRIS_TRANSPORT_KEY_EPOCH": "1",
        "OSIRIS_REPLAY_CACHE": str(tmp / f"{name}.replay"),
        "OSIRIS_PEER_KEY_STORE": str(tmp / f"{name}.peer-keys.sqlite3"),
        "OSIRIS_HEALTH_HTTP_ADDR": "127.0.0.1:0",
        "OSIRIS_LISTEN_ADDR": "/ip4/127.0.0.1/tcp/0",
        "OSIRIS_MDNS_ENABLED": "0",
        # DCUtR otherwise races publication by upgrading the local circuit to
        # a direct connection, invalidating relay-only delivery evidence.
        "OSIRIS_HOLE_PUNCH_ENABLED": "0",
        "OSIRIS_E2EE_ENABLED": "1",
    }


def assert_circuit_only(process: Process, other_peer: str) -> None:
    connections = [line for line in process.logs()
                   if line.startswith(f"peer_connection_path peer={other_peer} ")]
    if not connections or any("relayed=true" not in line for line in connections):
        raise AssertionError(
            f"{process.name}: expected only relayed connections to {other_peer}, "
            f"observed {connections}"
        )


def run(binary_dir: Path | None, timeout: float, evidence: dict) -> None:
    node_binary, relay_binary = find_binaries(binary_dir)
    evidence.update(source_revision())
    evidence["core_binary_sha256"] = binary_digest(node_binary)
    evidence["relay_binary_sha256"] = binary_digest(relay_binary)
    # Operator settings must not accidentally alter topology, recipients, keys,
    # or admission policy in this acceptance run.
    base = {name: value for name, value in os.environ.items()
            if not name.startswith("OSIRIS_")}
    processes: list[Process] = []
    with tempfile.TemporaryDirectory(prefix="osiris-core-relay-e2ee-") as raw_tmp:
        tmp = Path(raw_tmp)
        try:
            relay = Process("relay", relay_binary, base | {
                "OSIRIS_RELAY_SERVER_ENABLED": "true",
                "OSIRIS_RELAY_LISTEN_ADDR": "/ip4/127.0.0.1/tcp/0",
                "OSIRIS_RELAY_NODE_KEY": str(tmp / "relay.identity"),
            })
            processes.append(relay)
            relay_address = relay.prefix("relay_listening=", timeout).split("=", 1)[1]
            relay_peer = relay_address.rsplit("/p2p/", 1)[1]

            receiver = Process("recipient", node_binary,
                               core_environment(base, tmp, "recipient") | {
                                   "OSIRIS_RELAY_ADDRESS": relay_address,
                               })
            processes.append(receiver)
            receiver_peer = receiver.prefix("peer_id=", timeout).split("=", 1)[1]
            receiver.prefix("relay_reservation_accepted=true", timeout)
            circuit_address = f"{relay_address}/p2p-circuit/p2p/{receiver_peer}"

            sender = Process("sender", node_binary,
                             core_environment(base, tmp, "sender") | {
                                 "OSIRIS_BOOTSTRAP_PEERS": circuit_address,
                                 "OSIRIS_E2EE_RECIPIENTS": receiver_peer,
                             })
            processes.append(sender)
            sender_peer = sender.prefix("peer_id=", timeout).split("=", 1)[1]
            for process in (sender, receiver):
                process.wait_for(
                    lambda line: line.startswith("upnp_enabled=") and "hole_punch=disabled" in line,
                    timeout, "disabled automatic hole punching",
                )
            evidence.update({
                "relay_peer_id": relay_peer,
                "sender_peer_id": sender_peer,
                "recipient_peer_id": receiver_peer,
                "bootstrap_address": circuit_address,
            })
            sender.wait_for(
                lambda line: line.startswith(f"peer_connection_path peer={receiver_peer} ")
                and "relayed=true" in line, timeout, "sender's relayed peer connection",
            )
            receiver.wait_for(
                lambda line: line.startswith(f"peer_connection_path peer={sender_peer} ")
                and "relayed=true" in line, timeout, "recipient's relayed peer connection",
            )
            for process, peer in ((sender, receiver_peer), (receiver, sender_peer)):
                process.wait_for(
                    lambda line, peer=peer: line.startswith(f"key_binding_accepted peer={peer} ")
                    and "durable=true" in line, timeout, "durable authenticated recipient key",
                )

            payload = f"osiris-core-relay-e2ee-{evidence['run_id']}"
            digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            instrument = "P2P-CORE-RELAY-E2EE"
            observation = {
                "schema": "osiris.observation.v1",
                "instrument": instrument,
                "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "provider": "local-core-relay-acceptance",
                "payload": payload,
                "payload_sha256": digest,
            }
            assert sender.proc.stdin is not None
            sender.proc.stdin.write(json.dumps(observation) + "\n")
            sender.proc.stdin.flush()
            sender.wait_for(
                lambda line: line.startswith(f"encrypted_observation_published recipient={receiver_peer} "),
                timeout, "recipient-addressed encrypted publication",
            )
            receiver.wait_for(
                lambda line: line.startswith(f"encrypted_observation_received peer={sender_peer} ")
                and f"instrument={instrument} " in line
                and f"payload_sha256={digest} " in line,
                timeout, "exact decrypted observation digest",
            )
            assert_circuit_only(sender, receiver_peer)
            assert_circuit_only(receiver, sender_peer)
            if any(payload in line for process in processes for line in process.logs()):
                raise AssertionError("observation plaintext leaked to process logs")
            if any(process.proc.poll() is not None for process in processes):
                raise AssertionError("a process exited before successful acceptance")
            evidence.update({
                "status": "passed",
                "payload_sha256": digest,
                "checks": {
                    "relay_reservation": True,
                    "both_peer_connections_relayed": True,
                    "both_signed_bindings_durable": True,
                    "recipient_exact_digest": True,
                    "no_plaintext_payload_in_logs": True,
                    "mdns_disabled": True,
                    "hole_punch_disabled": True,
                },
            })
        finally:
            stop_all(processes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary-dir", type=Path)
    parser.add_argument("--timeout", type=float, default=45,
                        help="maximum seconds for each acceptance stage (default: 45)")
    parser.add_argument("--evidence", type=Path,
                        help="optional machine-readable JSON evidence file (public metadata only)")
    args = parser.parse_args()
    if not 1 <= args.timeout <= 180:
        parser.error("--timeout must be between 1 and 180 seconds")
    evidence = {
        "schema": "osiris.p2p.core-relay-e2ee-acceptance.v1",
        "run_id": uuid.uuid4().hex,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed",
        "scope": "single-host-process-integration",
    }
    exit_code = 0
    try:
        run(args.binary_dir, args.timeout, evidence)
    except (OSError, RuntimeError, TimeoutError, AssertionError) as error:
        evidence["error"] = str(error)
        print(f"FAIL: {error}", file=sys.stderr, flush=True)
        exit_code = 1
    finally:
        evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
        encoded = json.dumps(evidence, sort_keys=True, indent=2) + "\n"
        if args.evidence:
            args.evidence.parent.mkdir(parents=True, exist_ok=True)
            args.evidence.write_text(encoded, encoding="utf-8")
        print(f"CORE_RELAY_E2EE_EVIDENCE={json.dumps(evidence, sort_keys=True)}", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
