#!/usr/bin/env python3
"""Verify /ready tracks a configured bootstrap peer disconnecting and reconnecting."""
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
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
                    f"{self.name} exited {self.proc.returncode} waiting for {description}; "
                    f"logs={self.history}"
                )
            try:
                self.lines.get(timeout=0.1)
            except queue.Empty:
                pass
        raise TimeoutError(f"{self.name} timed out waiting for {description}; logs={self.history}")

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


def get_readiness(address):
    request = urllib.request.Request(f"http://{address}/ready", method="GET")
    try:
        response = urllib.request.urlopen(request, timeout=2)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.loads(response.read().decode("utf-8"))


def wait_for_readiness(address, expected_status, timeout, description):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = get_readiness(address)
            if last[0] == expected_status and last[1].get("ready") is (expected_status == 200):
                return last
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(0.2)
    raise TimeoutError(f"did not observe {description}; last response={last}")


def main():
    nodes = []
    try:
        with tempfile.TemporaryDirectory(prefix="osiris-bootstrap-readiness-") as tmp:
            tmp = Path(tmp)
            bootstrap_key = tmp / "bootstrap.key"
            bootstrap_addr = "/ip4/127.0.0.1/tcp/4022"

            def start_bootstrap(label):
                env = os.environ.copy()
                env["OSIRIS_NODE_KEY"] = str(bootstrap_key)
                env["OSIRIS_LISTEN_ADDR"] = bootstrap_addr
                env["OSIRIS_HEALTH_HTTP_ADDR"] = "127.0.0.1:0"
                node = Node(label, env)
                nodes.append(node)
                line = node.wait_for(lambda value: value.startswith("listener_ready ") and " address=" in value, 15, "bootstrap listener")
                return node, line.split(" address=", 1)[1]

            # Learn the persistent bootstrap identity/address, then leave it offline.
            candidate, multiaddr = start_bootstrap("bootstrap-identity")
            candidate.wait_for(lambda value: value.startswith("peer_id="), 10, "bootstrap Peer ID")
            candidate.stop()

            client_env = os.environ.copy()
            client_env["OSIRIS_NODE_KEY"] = str(tmp / "client.key")
            client_env["OSIRIS_LISTEN_ADDR"] = "/ip4/127.0.0.1/tcp/0"
            client_env["OSIRIS_BOOTSTRAP_PEERS"] = multiaddr
            client_env["OSIRIS_HEALTH_HTTP_ADDR"] = "127.0.0.1:0"
            client_env["OSIRIS_HEALTH_INTERVAL_SECS"] = "5"
            client = Node("client", client_env)
            nodes.append(client)
            health_line = client.wait_for(
                lambda value: value.startswith("health_http_listening="), 15, "client health listener"
            )
            health_address = health_line.split("=", 1)[1]

            status, snapshot = wait_for_readiness(
                health_address, 503, 10, "503 Not Ready while bootstrap is offline"
            )
            assert snapshot["bootstrap_peers_total"] == 1, snapshot
            assert snapshot["bootstrap_peers_connected"] == 0, snapshot
            print("BOOTSTRAP_OFFLINE_READINESS_503=PASS", flush=True)

            # Restart the same bootstrap identity and require readiness to turn green.
            restarted, restarted_addr = start_bootstrap("bootstrap-online")
            if restarted_addr.rsplit("/p2p/", 1)[-1] != multiaddr.rsplit("/p2p/", 1)[-1]:
                raise AssertionError("bootstrap Peer ID changed despite reusing its identity key")
            status, snapshot = wait_for_readiness(
                health_address, 200, 30, "200 Ready after bootstrap connection"
            )
            assert snapshot["bootstrap_peers_connected"] == 1, snapshot
            assert snapshot["bootstrap_peers_total"] == 1, snapshot
            print("BOOTSTRAP_ONLINE_READINESS_200=PASS", flush=True)
            print("P2P_BOOTSTRAP_READINESS_TRANSITION_TEST=PASS", flush=True)
    finally:
        for node in reversed(nodes):
            node.stop()


if __name__ == "__main__":
    main()
