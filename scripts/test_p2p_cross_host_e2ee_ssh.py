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
import queue
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


def pump(name, stream, events):
    for raw in iter(stream.readline, ""):
        line = raw.rstrip()
        print(f"[{name}] {line}", flush=True)
        events.put((name, line))
    events.put((name, "<stdout-closed>"))


def wait_for(events, processes, predicate, timeout, description):
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        try:
            name, line = events.get(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
        except queue.Empty:
            for process_name, proc in processes:
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"{process_name} exited with {proc.returncode}; recent output: {seen[-30:]}"
                    )
            continue
        seen.append(f"{name}: {line}")
        if predicate(name, line):
            return line
    raise TimeoutError(f"timed out waiting for {description}; recent output: {seen[-30:]}")


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

    events = queue.Queue()
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
            local_env = os.environ.copy()
            local_env.update({
                "OSIRIS_NODE_KEY": str(tmp / "host-a.identity"),
                "OSIRIS_TRANSPORT_KEY": str(tmp / "host-a.transport"),
                "OSIRIS_PEER_KEY_STORE": str(tmp / "host-a.peer-keys.sqlite3"),
                "OSIRIS_REPLAY_CACHE": str(tmp / "host-a.replay"),
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

            remote_binary = args.remote_binary
            if not remote_binary.startswith(("/", "./")):
                remote_binary = "./" + remote_binary
            remote_command = (
                "set -eu; cd \"$HOME\"; umask 077; tmp=$(mktemp -d /tmp/osiris-xhost-e2ee.XXXXXX); "
                "trap 'rm -rf \"$tmp\"' EXIT; "
                "export OSIRIS_NODE_KEY=\"$tmp/identity\" "
                "OSIRIS_TRANSPORT_KEY=\"$tmp/transport\" "
                "OSIRIS_PEER_KEY_STORE=\"$tmp/peer-keys.sqlite3\" "
                "OSIRIS_REPLAY_CACHE=\"$tmp/replay\" "
                "OSIRIS_E2EE_ENABLED=1 OSIRIS_LISTEN_ADDR=/ip4/0.0.0.0/tcp/0 "
                "OSIRIS_HEALTH_HTTP_ADDR=127.0.0.1:0 "
                "OSIRIS_BOOTSTRAP_PEERS=" + shlex.quote(bootstrap) + "; "
                + shlex.quote(remote_binary)
                + " & child=$!; "
                + "cleanup() { kill \\"$child\\" 2>/dev/null || true; "
                + "wait \\"$child\\" 2>/dev/null || true; rm -rf \\"$tmp\\"; }; "
                + "trap cleanup EXIT HUP INT TERM; wait \\"$child\\""
            )
            remote = subprocess.Popen(
                ["ssh", "-T", *args.ssh_option, args.ssh_target, remote_command],
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
