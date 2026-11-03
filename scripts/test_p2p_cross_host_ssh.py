#!/usr/bin/env python3
"""Run a real cross-host GossipSub exchange over SSH.

Run on host A. Host B must be reachable over SSH and have the same release
binary at --remote-binary. Host B dials host A's reachable TCP address.
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
            for name, proc in processes:
                if proc.poll() is not None:
                    raise RuntimeError(f"{name} exited with {proc.returncode}; recent output: {seen[-30:]}")
            continue
        seen.append(f"{name}: {line}")
        if predicate(name, line):
            return line
    raise TimeoutError(f"timed out waiting for {description}; recent output: {seen[-30:]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-target", required=True, help="SSH destination for host B, e.g. user@host-b")
    parser.add_argument("--public-ip", required=True, help="Host A IP reachable from host B")
    parser.add_argument("--port", type=int, default=4001, help="Inbound TCP port on host A (default: 4001)")
    parser.add_argument("--remote-binary", default="~/osiris-eye/target/release/osiris-p2p-node",
                        help="P2P release binary path on host B")
    parser.add_argument("--binary", help="Local P2P release binary; auto-detected if omitted")
    parser.add_argument("--ssh-option", action="append", default=[], help="Extra SSH option; repeat as needed")
    args = parser.parse_args()

    binary = Path(args.binary).expanduser() if args.binary else next((p for p in CANDIDATES if p.exists()), None)
    if binary is None or not binary.is_file():
        raise SystemExit("Local P2P binary not found. Build with cargo build --release --manifest-path services/p2p-node/Cargo.toml")
    if not (1 <= args.port <= 65535):
        raise SystemExit("--port must be between 1 and 65535")

    events = queue.Queue()
    processes = []
    local = remote = None
    remote_key = f"/tmp/osiris-cross-host-{uuid.uuid4().hex}.key"
    instrument = f"OSIRIS-XHOST-{uuid.uuid4().hex[:12].upper()}"
    payload = f"cross-host integrity probe {uuid.uuid4().hex}"
    digest = hashlib.sha256(payload.encode()).hexdigest()

    try:
        with tempfile.TemporaryDirectory(prefix="osiris-cross-host-") as tmp:
            env = os.environ.copy()
            env.update({
                "OSIRIS_NODE_KEY": str(Path(tmp) / "host-a.key"),
                "OSIRIS_LISTEN_ADDR": f"/ip4/0.0.0.0/tcp/{args.port}",
                "OSIRIS_EXTERNAL_ADDRS": f"/ip4/{args.public_ip}/tcp/{args.port}",
                "OSIRIS_HEALTH_HTTP_ADDR": "127.0.0.1:0",
            })
            local = subprocess.Popen(
                [str(binary)], cwd=ROOT, env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
            processes.append(("host-a", local))
            threading.Thread(target=pump, args=("host-a", local.stdout, events), daemon=True).start()

            peer_line = wait_for(events, processes, lambda n, l: n == "host-a" and l.startswith("peer_id="),
                                 20, "host A Peer ID")
            peer_id = peer_line.split("=", 1)[1]
            wait_for(events, processes, lambda n, l: n == "host-a" and l.startswith(f"listening=/ip4/0.0.0.0/tcp/{args.port}/"),
                     20, "host A TCP listener")
            bootstrap = f"/ip4/{args.public_ip}/tcp/{args.port}/p2p/{peer_id}"

            # Keep the remote key private and remove it when the SSH process exits.
            remote_command = "set -eu; umask 077; key=" + shlex.quote(remote_key) + "; " \
                + "trap 'rm -f \"$key\"' EXIT; : > \"$key\"; " \
                + "OSIRIS_NODE_KEY=\"$key\" OSIRIS_LISTEN_ADDR=/ip4/0.0.0.0/tcp/0 " \
                + "OSIRIS_BOOTSTRAP_PEERS=" + shlex.quote(bootstrap) + " " \
                + shlex.quote(args.remote_binary) 
            ssh = ["ssh", "-T", *args.ssh_option, args.ssh_target, remote_command]
            remote = subprocess.Popen(ssh, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, text=True, bufsize=1)
            processes.append(("host-b-ssh", remote))
            threading.Thread(target=pump, args=("host-b", remote.stdout, events), daemon=True).start()

            wait_for(events, processes,
                     lambda n, l: n == "host-a" and l.startswith("gossipsub_subscribed peer="),
                     45, "host B subscription on host A")
            wait_for(events, processes,
                     lambda n, l: n == "host-b" and l.startswith("peer_connected="),
                     45, "host B connection to host A")

            envelope = {
                "schema": "osiris.observation.v1",
                "instrument": instrument,
                "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "provider": "osiris-cross-host-acceptance-test",
                "payload_sha256": digest,
                "payload": payload,
            }
            remote.stdin.write(json.dumps(envelope, separators=(",", ":")) + "\n")
            remote.stdin.flush()

            wait_for(events, processes,
                     lambda n, l: n == "host-a" and "observation_received" in l
                     and instrument in l and digest in l,
                     30, "host A to receive host B's exact test observation")
            print("CROSS_HOST_P2P_EXCHANGE=PASS", flush=True)
            print(f"host_a_peer_id={peer_id}", flush=True)
            print(f"instrument={instrument}", flush=True)
            print(f"payload_sha256={digest}", flush=True)
    finally:
        for _, proc in (("host-b-ssh", remote), ("host-a", local)):
            if proc is not None and proc.poll() is None:
                proc.terminate()
        for name, proc in (("host-b-ssh", remote), ("host-a", local)):
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
        print(f"CROSS_HOST_P2P_EXCHANGE=FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
