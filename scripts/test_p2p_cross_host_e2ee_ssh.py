#!/usr/bin/env python3
"""Verify recipient-only E2EE between two real P2P hosts over SSH.

Run from host A. Host B must be SSH-reachable and have the same release binary
at --remote-binary. Host B dials host A's reachable TCP address. Both nodes use
fresh local identity, transport-key, replay-cache, and peer-key-store files.
"""
import argparse
import hashlib
import json
import os
import collections
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = [
    ROOT / "target" / "release" / "osiris-p2p-node",
    ROOT / "services" / "p2p-node" / "target" / "release" / "osiris-p2p-node",
]


MAX_LOG_LINE_CHARS = 16_384


class EventLog:
    """Bounded, shared history: waiting for A must not consume B's readiness.

    Losing output invalidates acceptance evidence, so an overflow fails the run
    rather than silently dropping events that another wait still needs.
    """

    def __init__(self, max_events=4096, forbidden_by_source=None):
        self.history = collections.deque(maxlen=max_events)
        self.condition = threading.Condition()
        self.failure = None
        self.forbidden_by_source = forbidden_by_source or {}

    def record(self, name, line, safe_line=None):
        with self.condition:
            safe = line if safe_line is None else safe_line
            if any(value in line for value in self.forbidden_by_source.get(name, ())):
                self.failure = f"{name} exposed forbidden observation plaintext"
                safe = "<forbidden observation plaintext redacted>"
            if len(line) > MAX_LOG_LINE_CHARS:
                self.failure = f"{name} exceeded the maximum output line length"
                safe = "<oversized output line redacted>"
            if len(self.history) == self.history.maxlen:
                self.failure = "acceptance output history capacity exceeded"
            self.history.append((name, safe))
            self.condition.notify_all()
        return safe


def pump(name, stream, events):
    in_private_key = False
    while True:
        raw = stream.readline(MAX_LOG_LINE_CHARS + 2)
        if not raw:
            break
        line = raw.rstrip("\r\n")
        if "-----BEGIN " in line and "PRIVATE KEY-----" in line:
            in_private_key = True
        if in_private_key:
            redacted = "<private key material redacted>"
            if "-----END " in line and "PRIVATE KEY-----" in line:
                in_private_key = False
        else:
            redacted = re.sub(
                r"(?i)((?:private[_ -]?key|secret|token|password)\s*[:=]\s*)\S+",
                r"\1<redacted>", line,
            )
        # Validate the original line before redaction; forbidden evidence cannot
        # disappear through log filtering or retention limits.
        safe = events.record(name, line, redacted)
        print(f"[{name}] {safe}", flush=True)
    events.record(name, "<stdout-closed>")


def wait_for(events, processes, predicate, timeout, description):
    deadline = time.monotonic() + timeout
    with events.condition:
        while True:
            recent = [f"{name}: {line}" for name, line in list(events.history)[-30:]]
            if events.failure:
                raise RuntimeError(f"{events.failure}; recent output: {recent}")
            for process_name, proc in processes:
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"{process_name} exited with {proc.returncode}; recent output: {recent}"
                    )
            if any(line == "<stdout-closed>" for _, line in events.history):
                raise RuntimeError(f"node output closed before acceptance completed; recent output: {recent}")
            for name, line in events.history:
                if predicate(name, line):
                    return line
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"timed out waiting for {description}; recent output: {recent}")
            events.condition.wait(timeout=min(0.25, remaining))


def remote_command(binary, bootstrap):
    """Launch from the remote home and clean child plus state on SSH teardown."""
    binary = binary if binary.startswith(("/", "./")) else "./" + binary
    return (
        'set -eu; cd "$HOME"; umask 077; '
        'tmp=$(mktemp -d /tmp/osiris-xhost-e2ee.XXXXXX); child=""; '
        'cleanup() { status=$?; trap - EXIT HUP INT TERM; '
        'if [ -n "$child" ]; then kill "$child" 2>/dev/null || true; '
        'for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do '
        'kill -0 "$child" 2>/dev/null || break; sleep 0.1; done; '
        'kill -KILL "$child" 2>/dev/null || true; '
        'wait "$child" 2>/dev/null || true; fi; rm -rf "$tmp"; exit "$status"; }; '
        "trap cleanup EXIT; trap 'exit 129' HUP; trap 'exit 130' INT; trap 'exit 143' TERM; "
        'unset OSIRIS_E2EE_RECIPIENTS OSIRIS_E2EE_RECIPIENTS_FILE OSIRIS_RELAY_ADDRESS '
        'OSIRIS_REVOCATION_AUTHORITY_PUBLIC_KEY OSIRIS_TRUSTED_PEERS '
        'OSIRIS_TRUSTED_PEERS_FILE OSIRIS_REVOKED_PEERS OSIRIS_REVOKED_PEERS_FILE '
        'OSIRIS_EXTERNAL_ADDRS; '
        'export OSIRIS_NODE_KEY="$tmp/identity" '
        'OSIRIS_TRANSPORT_KEY="$tmp/transport" '
        'OSIRIS_PEER_KEY_STORE="$tmp/peer-keys.sqlite3" '
        'OSIRIS_REPLAY_CACHE="$tmp/replay" '
        'OSIRIS_REVOCATION_REGISTRY="$tmp/revocations.json" '
        'OSIRIS_ROTATE_NODE_KEY=0 OSIRIS_TRANSPORT_KEY_EPOCH=1 '
        'OSIRIS_MDNS_ENABLED=0 '
        'OSIRIS_E2EE_ENABLED=1 OSIRIS_LISTEN_ADDR=/ip4/0.0.0.0/tcp/0 '
        'OSIRIS_HEALTH_HTTP_ADDR=127.0.0.1:0 '
        'OSIRIS_BOOTSTRAP_PEERS=' + shlex.quote(bootstrap) + '; '
        + shlex.quote(binary) + ' <&0 & child=$!; status=0; wait "$child" || status=$?; '
        'child=""; exit "$status"'
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-target", required=True, help="SSH destination, e.g. user@host-b")
    parser.add_argument("--public-ip", required=True, help="Host A address reachable from host B")
    parser.add_argument("--port", type=int, default=4001, help="Inbound TCP port on host A")
    parser.add_argument(
        "--remote-binary",
        default="osiris-eye/target/release/osiris-p2p-node",
        help="P2P release binary path on host B, relative to its home or absolute",
    )
    parser.add_argument("--binary", help="Local release binary; auto-detected if omitted")
    parser.add_argument("--ssh-option", action="append", default=[], help="Extra SSH option; repeat as needed")
    args = parser.parse_args()

    binary = Path(args.binary).expanduser() if args.binary else next(
        (candidate for candidate in CANDIDATES if candidate.exists()), None
    )
    if binary is None or not binary.is_file():
        raise SystemExit(
            "Local P2P binary not found. Build with cargo build --release "
            "--manifest-path services/p2p-node/Cargo.toml"
        )
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535")

    events = EventLog()
    processes = []
    local = remote = None
    instrument = f"OSIRIS-XHOST-E2EE-{uuid.uuid4().hex[:12].upper()}"
    payload = f"cross-host encrypted recipient-only probe {uuid.uuid4().hex}"
    digest = hashlib.sha256(payload.encode()).hexdigest()

    try:
        with tempfile.TemporaryDirectory(prefix="osiris-cross-host-e2ee-") as raw_tmp:
            tmp = Path(raw_tmp)
            recipients_file = tmp / "recipients.txt"
            recipients_file.write_text("", encoding="utf-8")
            # Never join an operator's configured network or reuse its durable
            # files merely because the acceptance command inherits app settings.
            local_env = {key: value for key, value in os.environ.items() if not key.startswith("OSIRIS_")}
            local_env.update({
                "OSIRIS_NODE_KEY": str(tmp / "host-a.identity"),
                "OSIRIS_TRANSPORT_KEY": str(tmp / "host-a.transport"),
                "OSIRIS_PEER_KEY_STORE": str(tmp / "host-a.peer-keys.sqlite3"),
                "OSIRIS_REPLAY_CACHE": str(tmp / "host-a.replay"),
                "OSIRIS_REVOCATION_REGISTRY": str(tmp / "host-a.revocations.json"),
                "OSIRIS_MDNS_ENABLED": "0",
                "OSIRIS_E2EE_ENABLED": "1",
                "OSIRIS_E2EE_RECIPIENTS_FILE": str(recipients_file),
                "OSIRIS_LISTEN_ADDR": f"/ip4/0.0.0.0/tcp/{args.port}",
                "OSIRIS_EXTERNAL_ADDRS": f"/ip4/{args.public_ip}/tcp/{args.port}",
                "OSIRIS_HEALTH_HTTP_ADDR": "127.0.0.1:0",
            })
            local = subprocess.Popen(
                [str(binary)], cwd=ROOT, env=local_env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
            processes.append(("host-a", local))
            threading.Thread(target=pump, args=("host-a", local.stdout, events), daemon=True).start()

            peer_line = wait_for(
                events, processes,
                lambda name, line: name == "host-a" and line.startswith("peer_id="),
                20, "host A PeerId",
            )
            host_a_peer = peer_line.split("=", 1)[1].strip()
            wait_for(
                events, processes,
                lambda name, line: name == "host-a"
                and line.startswith("listener_ready ")
                and f"/tcp/{args.port}" in line,
                20, "host A externally reachable listener",
            )
            bootstrap = f"/ip4/{args.public_ip}/tcp/{args.port}/p2p/{host_a_peer}"

            remote = subprocess.Popen(
                ["ssh", "-T", *args.ssh_option, args.ssh_target,
                 remote_command(args.remote_binary, bootstrap)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            processes.append(("host-b-ssh", remote))
            threading.Thread(target=pump, args=("host-b", remote.stdout, events), daemon=True).start()

            remote_peer_line = wait_for(
                events, processes,
                lambda name, line: name == "host-b" and line.startswith("peer_id="),
                30, "host B PeerId",
            )
            host_b_peer = remote_peer_line.split("=", 1)[1].strip()
            wait_for(
                events, processes,
                lambda name, line: name == "host-a"
                and f"key_binding_accepted peer={host_b_peer} " in line,
                45, "host A to verify and persist host B's signed key binding",
            )
            wait_for(
                events, processes,
                lambda name, line: name == "host-b"
                and f"key_binding_accepted peer={host_a_peer} " in line,
                45, "host B to verify and persist host A's signed key binding",
            )

            # Only host B is an intended recipient. No plaintext GossipSub
            # publication is used in this path.
            recipients_file.write_text(host_b_peer + "\n", encoding="utf-8")
            envelope = {
                "schema": "osiris.observation.v1",
                "instrument": instrument,
                "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "provider": "osiris-cross-host-e2ee-acceptance-test",
                "payload_sha256": digest,
                "payload": payload,
            }
            local.stdin.write(json.dumps(envelope, separators=(",", ":")) + "\n")
            local.stdin.flush()

            wait_for(
                events, processes,
                lambda name, line: name == "host-a"
                and f"encrypted_observation_published recipient={host_b_peer} " in line,
                20, "host A to publish the recipient-encrypted envelope",
            )
            wait_for(
                events, processes,
                lambda name, line: name == "host-b"
                and "encrypted_observation_received" in line
                and f"instrument={instrument}" in line
                and f"payload_sha256={digest}" in line,
                30, "host B to decrypt the exact cross-host payload",
            )
            print("CROSS_HOST_E2EE_RECIPIENT_DELIVERY=PASS", flush=True)
            print(f"host_a_peer_id={host_a_peer}", flush=True)
            print(f"host_b_peer_id={host_b_peer}", flush=True)
            print(f"instrument={instrument}", flush=True)
            print(f"payload_sha256={digest}", flush=True)
    finally:
        for _, proc in (("host-b-ssh", remote), ("host-a", local)):
            if proc is not None and proc.poll() is None:
                proc.terminate()
        for _, proc in (("host-b-ssh", remote), ("host-a", local)):
            if proc is not None:
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, TimeoutError, subprocess.SubprocessError) as exc:
        print(f"CROSS_HOST_E2EE_RECIPIENT_DELIVERY=FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
