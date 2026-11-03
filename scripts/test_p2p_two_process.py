#!/usr/bin/env python3
"""Launch two independent OSIRIS P2P node processes and verify GossipSub delivery."""
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "services" / "p2p-node" / "target" / "release" / "osiris-p2p-node"
# Cargo places package binaries in the workspace target directory when invoked
# with --manifest-path from the repository root.
if not BINARY.exists():
    BINARY = ROOT / "target" / "release" / "osiris-p2p-node"
if not BINARY.exists():
    print("P2P binary not found; build it before running this test", file=sys.stderr)
    sys.exit(2)

events = queue.Queue()
processes = []

def pump(name, stream):
    for line in iter(stream.readline, ""):
        events.put((name, line.rstrip()))
    events.put((name, "<stdout-closed>"))

def wait_for(predicate, timeout, description):
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        try:
            name, line = events.get(timeout=max(0.05, deadline - time.monotonic()))
        except queue.Empty:
            break
        seen.append(f"{name}: {line}")
        print(f"[{name}] {line}", flush=True)
        if predicate(name, line):
            return line
        if any(p.poll() is not None for p in processes):
            raise RuntimeError(f"a node exited early; output so far: {seen}")
    raise TimeoutError(f"timed out waiting for {description}; output so far: {seen}")

def start_node(name, key_path, listen=None, bootstrap=None):
    env = os.environ.copy()
    env["OSIRIS_NODE_KEY"] = str(key_path)
    if listen:
        env["OSIRIS_LISTEN_ADDR"] = listen
    if bootstrap:
        env["OSIRIS_BOOTSTRAP_PEERS"] = bootstrap
    proc = subprocess.Popen(
        [str(BINARY)], cwd=ROOT, env=env, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    processes.append(proc)
    threading.Thread(target=pump, args=(name, proc.stdout), daemon=True).start()
    return proc

try:
    with tempfile.TemporaryDirectory(prefix="osiris-p2p-process-") as tmp:
        tmp = Path(tmp)
        node_a = start_node("node-a", tmp / "node-a.key", "/ip4/127.0.0.1/tcp/0")
        listen_line = wait_for(lambda n, l: n == "node-a" and l.startswith("listening="), 15, "node A listener")
        multiaddr = listen_line.split("=", 1)[1]
        node_b = start_node("node-b", tmp / "node-b.key", "/ip4/127.0.0.1/tcp/0", multiaddr)

        wait_for(lambda n, l: n == "node-a" and l.startswith("gossipsub_subscribed peer="), 20, "GossipSub subscription from node B")
        envelope = {
            "schema": "osiris.observation.v1",
            "instrument": "P2P-PROCESS-TEST",
            "observed_at": "2026-11-03T01:00:00Z",
            "provider": "github-actions-two-process-test",
            "payload_sha256": "b" * 64,
        }
        node_a.stdin.write(json.dumps(envelope) + "\n")
        node_a.stdin.flush()
        wait_for(
            lambda n, l: n == "node-b" and "observation_received" in l and "P2P-PROCESS-TEST" in l,
            20,
            "node B to receive node A observation",
        )
        print("PASS: two separate OS processes exchanged a signed GossipSub observation.", flush=True)
finally:
    for proc in processes:
        if proc.poll() is None:
            proc.terminate()
    for proc in processes:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
