#!/usr/bin/env python3
"""Verify a P2P node reconnects after its configured bootstrap peer restarts."""
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
    raise SystemExit("P2P binary not found; run cargo build --release first")


class Node:
    def __init__(self, name, env):
        self.name = name
        self.proc = subprocess.Popen(
            [str(BINARY)], cwd=ROOT, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        self.history = []
        self.lines = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            line = line.rstrip()
            self.history.append(line)
            print(f"[{self.name}] {line}", flush=True)
            self.lines.put(line)

    def wait_for(self, predicate, timeout, description):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for line in self.history:
                if predicate(line):
                    return line
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"{self.name} exited {self.proc.returncode} while waiting for {description}; "
                    f"logs={self.history}"
                )
            try:
                self.lines.get(timeout=0.1)
            except queue.Empty:
                pass
        raise TimeoutError(f"{self.name} timed out waiting for {description}; logs={self.history}")

    def wait_for_count(self, predicate, count, timeout, description):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            matches = [line for line in self.history if predicate(line)]
            if len(matches) >= count:
                return matches
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"{self.name} exited {self.proc.returncode} while waiting for {description}; "
                    f"logs={self.history}"
                )
            time.sleep(0.1)
        raise TimeoutError(
            f"{self.name} saw {len([line for line in self.history if predicate(line)])}/{count} "
            f"events for {description}; logs={self.history}"
        )

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


def main():
    nodes = []
    try:
        with tempfile.TemporaryDirectory(prefix="osiris-bootstrap-restart-") as tmp:
            tmp = Path(tmp)
            bootstrap_key = tmp / "bootstrap.key"
            bootstrap_addr = "/ip4/127.0.0.1/tcp/4021"

            def start_bootstrap(label):
                env = os.environ.copy()
                env["OSIRIS_NODE_KEY"] = str(bootstrap_key)
                env["OSIRIS_LISTEN_ADDR"] = bootstrap_addr
                env["OSIRIS_HEALTH_HTTP_ADDR"] = "127.0.0.1:0"
                env["OSIRIS_BOOTSTRAP_RECONNECT_INTERVAL_SECS"] = "5"
                node = Node(label, env)
                nodes.append(node)
                line = node.wait_for(
                    lambda value: value.startswith("listener_ready ") and " address=" in value,
                    15,
                    "bootstrap listener",
                )
                return node, line.split(" address=", 1)[1]

            bootstrap, multiaddr = start_bootstrap("bootstrap-first")
            client_env = os.environ.copy()
            client_env["OSIRIS_NODE_KEY"] = str(tmp / "client.key")
            client_env["OSIRIS_LISTEN_ADDR"] = "/ip4/127.0.0.1/tcp/0"
            client_env["OSIRIS_HEALTH_HTTP_ADDR"] = "127.0.0.1:0"
            client_env["OSIRIS_BOOTSTRAP_RECONNECT_INTERVAL_SECS"] = "5"
            client_env["OSIRIS_BOOTSTRAP_PEERS"] = multiaddr
            client = Node("client", client_env)
            nodes.append(client)

            peer_line = bootstrap.wait_for(
                lambda value: value.startswith("peer_id="), 10, "bootstrap Peer ID"
            )
            bootstrap_peer_id = peer_line.split("=", 1)[1]
            client.wait_for(
                lambda value: value == "bootstrap_reconnect_interval_seconds=5",
                10, "configured reconnect interval",
            )
            client.wait_for(
                lambda value: value == f"peer_connected={bootstrap_peer_id}",
                20, "initial bootstrap connection",
            )
            print("INITIAL_BOOTSTRAP_CONNECTION=PASS", flush=True)

            bootstrap.stop()
            client.wait_for(
                lambda value: value.startswith(f"peer_disconnected={bootstrap_peer_id} "),
                15, "bootstrap disconnect detection",
            )
            print("BOOTSTRAP_DISCONNECT_DETECTED=PASS", flush=True)

            time.sleep(1)
            restarted, restarted_addr = start_bootstrap("bootstrap-restarted")
            if restarted_addr.rsplit("/p2p/", 1)[-1] != bootstrap_peer_id:
                raise AssertionError("bootstrap Peer ID changed after restart with persisted identity")

            connected_events_before_restart = sum(
                value == f"peer_connected={bootstrap_peer_id}" for value in client.history
            )
            client.wait_for_count(
                lambda value: value == f"peer_connected={bootstrap_peer_id}",
                connected_events_before_restart + 1,
                45,
                "automatic reconnection to restarted bootstrap",
            )
            print("BOOTSTRAP_AUTOMATIC_RECONNECTION=PASS", flush=True)
            print("P2P_BOOTSTRAP_RESTART_RECOVERY_TEST=PASS", flush=True)
    finally:
        for node in reversed(nodes):
            node.stop()


if __name__ == "__main__":
    main()
