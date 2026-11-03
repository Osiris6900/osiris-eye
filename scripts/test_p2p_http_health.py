#!/usr/bin/env python3
"""Verify the P2P node exposes a local, machine-readable HTTP health endpoint."""
import json
import os
import queue
import subprocess
import sys
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


def main():
    with tempfile.TemporaryDirectory(prefix="osiris-health-http-") as tmp:
        env = os.environ.copy()
        env["OSIRIS_NODE_KEY"] = str(Path(tmp) / "node.key")
        env["OSIRIS_LISTEN_ADDR"] = "/ip4/127.0.0.1/tcp/0"
        env["OSIRIS_HEALTH_HTTP_ADDR"] = "127.0.0.1:0"
        env["OSIRIS_HEALTH_INTERVAL_SECS"] = "5"
        proc = subprocess.Popen(
            [str(BINARY)], cwd=ROOT, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        lines = queue.Queue()

        def pump():
            for line in proc.stdout:
                line = line.rstrip()
                print(f"[node] {line}", flush=True)
                lines.put(line)

        threading.Thread(target=pump, daemon=True).start()
        try:
            deadline = time.monotonic() + 15
            address = None
            peer_id = None
            while time.monotonic() < deadline and (address is None or peer_id is None):
                if proc.poll() is not None:
                    raise RuntimeError(f"node exited early with code {proc.returncode}")
                try:
                    line = lines.get(timeout=0.2)
                except queue.Empty:
                    continue
                if line.startswith("health_http_listening="):
                    address = line.split("=", 1)[1]
                elif line.startswith("peer_id="):
                    peer_id = line.split("=", 1)[1]
            if address is None or peer_id is None:
                raise TimeoutError("node did not announce its HTTP health listener and Peer ID")

            url = f"http://{address}/health"
            deadline = time.monotonic() + 10
            payload = None
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen(url, timeout=2) as response:
                        if response.status != 200:
                            raise AssertionError(f"unexpected health status: {response.status}")
                        payload = json.loads(response.read().decode("utf-8"))
                    break
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    time.sleep(0.1)
            if payload is None:
                raise TimeoutError("health endpoint did not become available")

            assert payload["status"] == "alive", payload
            assert payload["peer_id"] == peer_id, payload
            assert isinstance(payload["uptime_seconds"], int), payload
            assert isinstance(payload["connected_peers"], int), payload
            assert isinstance(payload["bootstrap_peers_connected"], int), payload
            assert isinstance(payload["bootstrap_peers_total"], int), payload
            assert payload["connected_peers"] >= 0, payload
            print("P2P_HTTP_HEALTH_ENDPOINT=PASS", flush=True)
        finally:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


if __name__ == "__main__":
    main()
