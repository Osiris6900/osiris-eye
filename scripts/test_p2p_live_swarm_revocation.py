#!/usr/bin/env python3
"""Three live node processes: signed revocation disconnects a connected peer and blocks it."""
import hashlib
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARGO = os.environ.get("CARGO", "cargo")
BINARY = ROOT / "services" / "p2p-node" / "target" / "release" / "osiris-p2p-node"
if not BINARY.exists():
    BINARY = ROOT / "target" / "release" / "osiris-p2p-node"
PROCESSES = []

class Node:
    def __init__(self, name, env):
        self.name, self.lines, self.history = name, queue.Queue(), []
        self.proc = subprocess.Popen([str(BINARY)], cwd=ROOT, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        PROCESSES.append(self.proc)
        threading.Thread(target=self._pump, daemon=True).start()
    def _pump(self):
        for raw in self.proc.stdout:
            line = raw.rstrip()
            self.history.append(line)
            print(f"[{self.name}] {line}", flush=True)
            self.lines.put(line)
    def wait(self, predicate, timeout, description):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if any(predicate(line) for line in self.history):
                return
            if self.proc.poll() is not None:
                raise RuntimeError(f"{self.name} exited {self.proc.returncode}: {self.history}")
            try:
                self.lines.get(timeout=.2)
            except queue.Empty:
                pass
        raise TimeoutError(f"{self.name} missing {description}; logs={self.history}")
    def peer_id(self):
        self.wait(lambda line: line.startswith("peer_id="), 15, "peer id")
        return next(line.split("=", 1)[1] for line in self.history if line.startswith("peer_id="))
    def address(self):
        self.wait(lambda line: line.startswith("listener_ready ") and " address=" in line, 15, "listener")
        line = next(line for line in self.history if line.startswith("listener_ready ") and " address=" in line)
        return line.split(" address=", 1)[1].split()[0]
    def send(self, value):
        self.proc.stdin.write(json.dumps(value) + "\n")
        self.proc.stdin.flush()

def stop_all():
    for proc in PROCESSES:
        if proc.poll() is None:
            proc.terminate()
    for proc in PROCESSES:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

def main():
    if not BINARY.exists():
        raise SystemExit(f"missing {BINARY}; build release node first")
    with tempfile.TemporaryDirectory(prefix="osiris-live-revocation-") as raw:
        td = Path(raw)
        base = os.environ.copy()
        authority = "00"  # replaced from signed fixture output
        def node_env(name, bootstrap=None, authority_key=None, registry=None):
            env = base.copy()
            env.update({
                "OSIRIS_NODE_KEY": str(td / f"{name}.key"),
                "OSIRIS_PEER_KEY_STORE": str(td / f"{name}.peer-keys.sqlite3"),
                "OSIRIS_LISTEN_ADDR": "/ip4/127.0.0.1/tcp/0",
                "OSIRIS_HEALTH_HTTP_ADDR": "127.0.0.1:0",
                "OSIRIS_REPLAY_CACHE": str(td / f"{name}.replay"),
                "OSIRIS_REVOCATION_SYNC_INTERVAL_SECS": "30",
            })
            if bootstrap:
                env["OSIRIS_BOOTSTRAP_PEERS"] = bootstrap
            if authority_key:
                env["OSIRIS_REVOCATION_AUTHORITY_PUBLIC_KEY"] = authority_key
            if registry:
                env["OSIRIS_REVOCATION_REGISTRY"] = str(registry)
            return env

        # Start the receiver first, then the peer that will be revoked.
        receiver = Node("receiver", node_env("receiver"))
        receiver_addr = receiver.address()
        target = Node("target", node_env("target", bootstrap=receiver_addr))
        target_peer = target.peer_id()
        target.wait(lambda line: f"peer_connected=" in line and receiver.peer_id() in line, 20,
                    "target connected to receiver")
        receiver.wait(lambda line: f"peer_connected={target_peer}" in line, 20,
                      "receiver connected to target")

        fixture_path = td / "authority.json"
        fixture = subprocess.run(
            [CARGO, "run", "--quiet", "--manifest-path",
             str(ROOT / "services/p2p-node/Cargo.toml"), "--example", "make_revocation_fixture",
             "--", str(fixture_path), target_peer],
            cwd=ROOT, text=True, capture_output=True, check=True,
        )
        values = dict(line.split("=", 1) for line in fixture.stdout.splitlines() if "=" in line)
        authority = values["authority_public_key"]
        assert values["target_peer_id"] == target_peer

        # The authority node starts with a durable, valid revocation for the
        # already-connected target and propagates it through GossipSub to receiver.
        authority_node = Node("authority", node_env("authority", bootstrap=receiver_addr,
            authority_key=authority, registry=fixture_path))
        authority_node.wait(lambda line: line.startswith("signed_revocation_registry=loaded records=1"), 15,
                            "authority registry loaded")
        receiver.wait(lambda line: f"signed_peer_revocation_applied peer={target_peer}" in line,
                      45, "receiver applied signed revocation")
        receiver.wait(lambda line: f"peer_disconnected={target_peer}" in line,
                      15, "receiver disconnected revoked peer")

        # The target's periodic bootstrap reconnect must be rejected by the
        # receiver's persisted runtime deny policy.
        receiver.wait(lambda line: f"peer_rejected={target_peer} reason=not_in_trusted_peer_allowlist" in line,
                      45, "revoked peer reconnect rejected")

        payload = "revoked peer must not deliver this observation"
        observation = {
            "schema": "osiris.observation.v1",
            "instrument": "REVOKED-PEER-NEGATIVE-TEST",
            "observed_at": "2026-11-04T00:00:00Z",
            "provider": "github-actions-live-revocation-test",
            "payload": payload,
            "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        }
        target.send(observation)
        receiver.wait(lambda line: f"message_rejected peer={target_peer} reason=not_in_trusted_peer_allowlist" in line,
                      20, "post-revocation message rejected")
        assert not any("observation_received" in line and "REVOKED-PEER-NEGATIVE-TEST" in line
                       for line in receiver.history), "revoked peer observation reached acceptance path"
        print("LIVE_SWARM_SIGNED_REVOCATION_DISCONNECT_RECONNECT_DENY=PASS", flush=True)

if __name__ == "__main__":
    try:
        main()
    finally:
        stop_all()
