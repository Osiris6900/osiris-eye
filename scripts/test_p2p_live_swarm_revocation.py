#!/usr/bin/env python3
"""Three live node processes: signed revocation disconnects a connected peer and blocks it."""
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

def make_fixture(path, target_peer=None):
    command = [CARGO, "run", "--quiet", "--manifest-path",
        str(ROOT / "services/p2p-node/Cargo.toml"), "--example", "make_revocation_fixture", "--", str(path)]
    if target_peer:
        command.append(target_peer)
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=True)
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

def main():
    if not BINARY.exists():
        raise SystemExit(f"missing {BINARY}; build release node first")
    with tempfile.TemporaryDirectory(prefix="osiris-live-revocation-") as raw:
        td = Path(raw)
        # The test authority is deterministic and test-only. Obtain its public key
        # before starting nodes so receiver and target both validate the event.
        authority = make_fixture(td / "placeholder.json")["authority_public_key"]
        base = os.environ.copy()
        def node_env(name, bootstrap=None, registry=None):
            env = base.copy()
            env.update({
                "OSIRIS_NODE_KEY": str(td / f"{name}.key"),
                "OSIRIS_PEER_KEY_STORE": str(td / f"{name}.peer-keys.sqlite3"),
                "OSIRIS_LISTEN_ADDR": "/ip4/127.0.0.1/tcp/0",
                "OSIRIS_HEALTH_HTTP_ADDR": "127.0.0.1:0",
                "OSIRIS_REPLAY_CACHE": str(td / f"{name}.replay"),
                "OSIRIS_REVOCATION_SYNC_INTERVAL_SECS": "30",
                "OSIRIS_REVOCATION_AUTHORITY_PUBLIC_KEY": authority,
                "OSIRIS_REVOCATION_REGISTRY": str(registry or (td / f"{name}.revocations.json")),
            })
            if bootstrap:
                env["OSIRIS_BOOTSTRAP_PEERS"] = bootstrap
            return env

        receiver = Node("receiver", node_env("receiver"))
        receiver_addr = receiver.address()
        receiver_peer = receiver.peer_id()
        target = Node("target", node_env("target", bootstrap=receiver_addr))
        target_peer = target.peer_id()
        target.wait(lambda line: f"peer_connected={receiver_peer}" in line, 20,
                    "target connected to receiver")
        receiver.wait(lambda line: f"peer_connected={target_peer}" in line, 20,
                      "receiver connected to target")

        fixture_path = td / "authority.json"
        values = make_fixture(fixture_path, target_peer)
        assert values["authority_public_key"] == authority
        assert values["target_peer_id"] == target_peer

        # The authority node advertises a valid signed record through the real
        # revocation GossipSub topic to a receiver already connected to target.
        authority_node = Node("authority", node_env("authority", bootstrap=receiver_addr,
            registry=fixture_path))
        authority_node.wait(lambda line: line.startswith("signed_revocation_registry=loaded records=1"), 15,
                            "authority registry loaded")
        receiver.wait(lambda line: f"signed_peer_revocation_applied peer={target_peer}" in line,
                      45, "receiver applied signed revocation")
        receiver.wait(lambda line: f"peer_disconnected={target_peer}" in line,
                      15, "receiver disconnected revoked peer")

        # Target periodically redials its bootstrap. Receiver must reject the
        # connection using the runtime deny set populated by the signed event.
        receiver.wait(lambda line: f"peer_rejected={target_peer} reason=not_in_trusted_peer_allowlist" in line,
                      45, "revoked peer reconnect rejected")
        print("LIVE_SWARM_SIGNED_REVOCATION_DISCONNECT_RECONNECT_DENY=PASS", flush=True)

if __name__ == "__main__":
    try:
        main()
    finally:
        stop_all()
