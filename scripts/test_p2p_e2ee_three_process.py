#!/usr/bin/env python3
"""Verify recipient E2EE delivery to one node and rejection by a non-recipient."""
import hashlib
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
BINARY = ROOT / "target" / "release" / "osiris-p2p-node"
if not BINARY.exists():
    BINARY = ROOT / "services" / "p2p-node" / "target" / "release" / "osiris-p2p-node"
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
        if any(proc.poll() is not None for proc in processes):
            raise RuntimeError(f"a node exited early; output so far: {seen}")
    raise TimeoutError(f"timed out waiting for {description}; output so far: {seen}")

def start_node(name, tmp, listen=None, bootstrap=None, recipients_file=None):
    env = os.environ.copy()
    env["OSIRIS_NODE_KEY"] = str(tmp / f"{name}.identity")
    env["OSIRIS_TRANSPORT_KEY"] = str(tmp / f"{name}.transport")
    env["OSIRIS_REPLAY_CACHE"] = str(tmp / f"{name}.replay")
    env["OSIRIS_HEALTH_HTTP_ADDR"] = "127.0.0.1:0"
    env["OSIRIS_E2EE_ENABLED"] = "1"
    if recipients_file:
        env["OSIRIS_E2EE_RECIPIENTS_FILE"] = str(recipients_file)
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

def peer_id(name):
    line = wait_for(lambda n, l: n == name and l.startswith("peer_id="), 15, f"{name} PeerId")
    return line.split("=", 1)[1].strip()

try:
    with tempfile.TemporaryDirectory(prefix="osiris-p2p-e2ee-") as raw_tmp:
        tmp = Path(raw_tmp)
        recipients_file = tmp / "recipients.txt"
        recipients_file.write_text("", encoding="utf-8")

        node_a = start_node("node-a", tmp, "/ip4/127.0.0.1/tcp/0", recipients_file=recipients_file)
        listen_line = wait_for(
            lambda n, l: n == "node-a" and l.startswith("listener_ready ") and " address=" in l,
            15, "node A listener"
        )
        multiaddr = listen_line.split(" address=", 1)[1].split()[0]
        node_a_peer = multiaddr.rsplit("/p2p/", 1)[1]

        node_b = start_node("node-b", tmp, "/ip4/127.0.0.1/tcp/0", multiaddr)
        node_b_peer = peer_id("node-b")
        node_c = start_node("node-c", tmp, "/ip4/127.0.0.1/tcp/0", multiaddr)
        node_c_peer = peer_id("node-c")

        wait_for(lambda n, l: n == "node-a" and f"key_binding_accepted peer={node_b_peer} " in l,
                 25, "node A to accept node B's signed key binding")
        wait_for(lambda n, l: n == "node-b" and f"key_binding_accepted peer={node_a_peer} " in l,
                 25, "node B to accept node A's signed key binding")
        wait_for(lambda n, l: n == "node-c" and f"key_binding_accepted peer={node_a_peer} " in l,
                 25, "node C to accept node A's signed key binding")
        wait_for(lambda n, l: n == "node-a" and f"key_binding_accepted peer={node_c_peer} " in l,
                 25, "node A to accept node C's signed key binding")

        recipients_file.write_text(node_b_peer + "\n", encoding="utf-8")
        payload = "recipient-only encrypted observation"
        observation = {
            "schema": "osiris.observation.v1",
            "instrument": "P2P-E2EE-TEST",
            "observed_at": "2026-11-03T20:00:00Z",
            "provider": "github-actions-e2ee-test",
            "payload": payload,
            "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        }
        node_a.stdin.write(json.dumps(observation) + "\n")
        node_a.stdin.flush()

        wait_for(lambda n, l: n == "node-b" and "encrypted_observation_received" in l
                 and "P2P-E2EE-TEST" in l, 25, "recipient node B to decrypt observation")
        wait_for(lambda n, l: n == "node-c" and
                 "encrypted_message_discarded reason=not_for_this_recipient_or_sender_mismatch" in l,
                 25, "non-recipient node C to discard ciphertext")

        print("PASS: recipient decrypted the E2EE observation; non-recipient did not.", flush=True)
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
