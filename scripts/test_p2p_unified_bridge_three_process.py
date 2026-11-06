#!/usr/bin/env python3
"""Launch three OSIRIS node processes and verify atomic unified-bridge delivery."""
import json
import hashlib
import os
import queue
import sqlite3
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
    env["OSIRIS_REPLAY_CACHE"] = str(key_path.with_suffix(".replay-cache"))
    env["OSIRIS_TRANSPORT_KEY"] = str(key_path.with_suffix(".transport"))
    env["OSIRIS_PEER_KEY_STORE"] = str(key_path.with_suffix(".peer-keys.sqlite3"))
    if listen:
        env["OSIRIS_LISTEN_ADDR"] = listen
    if bootstrap:
        env["OSIRIS_BOOTSTRAP_PEERS"] = bootstrap
    # Each process must bind its own ephemeral health port in shared CI hosts.
    env["OSIRIS_HEALTH_HTTP_ADDR"] = "127.0.0.1:0"
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
        # The node emits structured listener diagnostics. Keep the complete
        # /p2p/<peer-id> suffix so node B can dial the exact authenticated peer.
        listen_line = wait_for(
            lambda name, line: name == "node-a" and line.startswith("listener_ready ") and " address=" in line,
            15,
            "node A listener",
        )
        multiaddr = listen_line.split(" address=", 1)[1].split()[0]
        node_b = start_node("node-b", tmp / "node-b.key", "/ip4/127.0.0.1/tcp/0", multiaddr)
        node_c = start_node("node-c", tmp / "node-c.key", "/ip4/127.0.0.1/tcp/0", multiaddr)

        # A's subscription event confirms that the remote B peer has joined
        # the observation topic. GossipSub does not guarantee a symmetric
        # subscription event is emitted to both processes at the same time.
        wait_for(lambda name, line: name == "node-a" and line.startswith("gossipsub_subscribed peer="), 30, "GossipSub subscription from node B")
        wait_for(lambda name, line: name == "node-c" and "peer_connected=" in line, 30, "node C connection")
        # Allow a heartbeat for the mesh to settle before publishing.
        time.sleep(2.0)
        # Feed a real P2P node through the validated unified Python bridge.
        # No live market API is called; this is a deterministic network test.
        sys.path.insert(0, str(ROOT / "services" / "quant-api" / "src"))
        from osiris_api.data_store import ResearchStore
        from osiris_api.observation_outbox import ObservationOutbox
        from osiris_api.p2p_ingestion import DATASET
        from osiris_api.unified_observation_bridge import broadcast_unified_cycle

        os.environ["OSIRIS_UNIFIED_MESH_BROADCAST"] = "1"
        os.environ["OSIRIS_P2P_INGEST_ENABLED"] = "1"
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).replace(microsecond=0)
        def make_observation(provider, instrument):
            payload = "OSIRIS unified bridge end-to-end probe"
            return {
                "schema": "osiris.observation.v1",
                "instrument": instrument,
                "observed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "provider": provider,
                "payload": payload,
                "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
            }

        good = make_observation("binance-public", "UNIFIED-MARKET-E2E")
        world = make_observation("usgs-earthquakes", "UNIFIED-WORLD-E2E")
        celestial = make_observation("astro-computed", "UNIFIED-CELESTIAL-E2E")
        bad = dict(make_observation("binance-public", "UNIFIED-TAMPERED"))
        bad["payload"] = "altered after hashing"
        collectors = {
            "market": (
                lambda **kw: {"observations": [bad, good], "errors": []},
                frozenset({"binance-public"}),
            ),
        }
        collectors["world"] = (
            lambda **kw: {"observations": [world], "errors": []},
            frozenset({"usgs-earthquakes"}),
        )
        collectors["celestial"] = (
            lambda **kw: {"observations": [celestial], "errors": []},
            frozenset({"astro-computed"}),
        )
        store = ResearchStore(str(tmp / "unified.sqlite3"))
        outbox = ObservationOutbox(store.database_path)
        def write_validated(line):
            observation = json.loads(line)
            # Both records must be committed and visible to independent readers
            # before the first byte is handed to the real local node.
            assert any(
                row["record_key"] == (
                    f"{observation['instrument']}:{observation['payload_sha256'][:32]}"
                )
                and row["payload"]["provider"] == observation["provider"]
                and row["payload"]["payload_sha256"] == observation["payload_sha256"]
                for row in store.list(DATASET)
            ), "research observation was not committed before publication"
            with sqlite3.connect(store.database_path) as reader:
                assert reader.execute(
                    "SELECT 1 FROM pending_observations WHERE observation_json=? "
                    "AND delivered=0 AND rejected_reason IS NULL", (line,),
                ).fetchone(), "pending delivery was not committed before publication"
            node_a.stdin.write(line + "\n")
            node_a.stdin.flush()
        result = broadcast_unified_cycle(
            write_validated, store=store, collectors=collectors, now=now, outbox=outbox,
        )
        assert result["broadcast"] == 3, result
        assert result["ingested"] == 3, result
        assert result["rejected"] == 1, result
        assert result["outbox_rejected"] == 0, result
        assert outbox.pending_count() == 0
        assert outbox.rejected_count() == 0
        # One queue consumer collects every receipt, avoiding lost events when
        # multiple nodes and sources deliver concurrently.
        expected = {
            (node, instrument)
            for node in ("node-b", "node-c")
            for instrument in (
                "UNIFIED-MARKET-E2E", "UNIFIED-WORLD-E2E",
                "UNIFIED-CELESTIAL-E2E",
            )
        }
        received = set()
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and received != expected:
            try:
                name, line = events.get(timeout=1)
            except queue.Empty:
                continue
            print(f"[{name}] {line}", flush=True)
            if "observation_received" in line:
                for node, instrument in expected - received:
                    if name == node and instrument in line:
                        received.add((node, instrument))
            if any(p.poll() is not None for p in processes):
                raise RuntimeError("node exited before all observations arrived")
        assert received == expected, f"missing receipts: {expected - received}"
        print("PASS: all three atomically queued sources reached both real peer nodes; tampered data blocked.", flush=True)
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
