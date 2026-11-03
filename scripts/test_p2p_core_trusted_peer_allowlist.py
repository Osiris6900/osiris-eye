#!/usr/bin/env python3
"""Verify the core P2P node disconnects a valid but untrusted peer."""
import os
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BINARY_CANDIDATES = [
    ROOT / "target" / "release" / "osiris-p2p-node",
    ROOT / "services" / "p2p-node" / "target" / "release" / "osiris-p2p-node",
]
BINARY = next((path for path in BINARY_CANDIDATES if path.is_file()), None)
if BINARY is None:
    raise SystemExit("P2P release binary not found; build it before running this test")

events = queue.Queue()
processes = []


def pump(name, stream):
    for line in iter(stream.readline, ""):
        events.put((name, line.rstrip()))
    events.put((name, "<stdout-closed>"))


def start_node(name, key_path, listen, bootstrap=None, trusted=None):
    env = os.environ.copy()
    env.update({
        "OSIRIS_NODE_KEY": str(key_path),
        "OSIRIS_LISTEN_ADDR": listen,
        "OSIRIS_HEALTH_HTTP_ADDR": "127.0.0.1:0",
    })
    if bootstrap:
        env["OSIRIS_BOOTSTRAP_PEERS"] = bootstrap
    if trusted is not None:
        env["OSIRIS_TRUSTED_PEERS"] = trusted
    proc = subprocess.Popen(
        [str(BINARY)], cwd=ROOT, env=env, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    processes.append((name, proc))
    threading.Thread(target=pump, args=(name, proc.stdout), daemon=True).start()
    return proc


def wait_for(predicate, timeout, description):
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        try:
            name, line = events.get(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
        except queue.Empty:
            for proc_name, proc in processes:
                if proc.poll() is not None:
                    raise RuntimeError(f"{proc_name} exited with {proc.returncode}; recent output: {seen[-25:]}")
            continue
        seen.append(f"{name}: {line}")
        print(f"[{name}] {line}", flush=True)
        if predicate(name, line):
            return line
    raise TimeoutError(f"timed out waiting for {description}; recent output: {seen[-25:]}")


def stop(proc):
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


try:
    with tempfile.TemporaryDirectory(prefix="osiris-p2p-allowlist-") as temp:
        temp = Path(temp)

        # Obtain a syntactically valid PeerId that is deliberately NOT node B.
        candidate = start_node("allowlisted-candidate", temp / "candidate.key", "/ip4/127.0.0.1/tcp/0")
        candidate_line = wait_for(
            lambda name, line: name == "allowlisted-candidate" and line.startswith("peer_id="),
            15, "candidate PeerId",
        )
        allowed_peer = candidate_line.split("=", 1)[1]
        stop(candidate)

        node_a = start_node(
            "node-a", temp / "node-a.key", "/ip4/127.0.0.1/tcp/0",
            trusted=allowed_peer,
        )
        listen_line = wait_for(
            lambda name, line: name == "node-a" and line.startswith("listener_ready ")
            and " address=" in line,
            15, "allowlisted node listener",
        )
        # The structured listener log includes the complete /p2p/<PeerId>
        # suffix required by the bootstrap dialer.
        bootstrap = listen_line.split(" address=", 1)[1]

        node_b = start_node(
            "node-b", temp / "node-b.key", "/ip4/127.0.0.1/tcp/0",
            bootstrap=bootstrap,
        )
        rejected_line = wait_for(
            lambda name, line: name == "node-a"
            and line.startswith("peer_rejected=")
            and "reason=not_in_trusted_peer_allowlist" in line,
            25, "node A to reject node B",
        )
        rejected_peer = rejected_line.split("=", 1)[1].split(" ", 1)[0]
        if rejected_peer == allowed_peer:
            raise RuntimeError("test setup error: node B unexpectedly used the allowlisted candidate PeerId")
        print("CORE_NODE_TRUSTED_PEER_ALLOWLIST=PASS", flush=True)
        print(f"allowlisted_peer_id={allowed_peer}", flush=True)
        print(f"rejected_peer_id={rejected_peer}", flush=True)
finally:
    for _, proc in reversed(processes):
        stop(proc)
