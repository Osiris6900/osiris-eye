#!/usr/bin/env python3
"""Exercise Circuit Relay v2 with three independent OS processes on localhost.

This validates relay reservations and relayed peer-to-peer delivery on one host.
It does not validate public Internet reachability, NAT traversal, or production hosting.
"""
import os
import queue
import subprocess
import threading
import time
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "services" / "p2p-node" / "target" / "release"
RELAY = BIN / "relay"
CLIENT = BIN / "relay-client"
PROCESSES = []


class Process:
    def __init__(self, name, command, env):
        self.name = name
        self.proc = subprocess.Popen(
            command, cwd=ROOT, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1,
        )
        self.lines = queue.Queue()
        self.history = []
        PROCESSES.append(self.proc)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            line = line.rstrip()
            self.history.append(line)
            print(f"[{self.name}] {line}", flush=True)
            self.lines.put(line)

    def wait_for(self, needle, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(needle in line for line in self.history):
                return next(line for line in self.history if needle in line)
            if self.proc.poll() is not None:
                raise RuntimeError(f"{self.name} exited {self.proc.returncode}; expected {needle}; logs={self.history}")
            try:
                line = self.lines.get(timeout=0.2)
                if needle in line:
                    return line
            except queue.Empty:
                pass
        raise TimeoutError(f"{self.name} did not emit {needle}; logs={self.history}")


def stop_all():
    for proc in PROCESSES:
        if proc.poll() is None:
            proc.terminate()
    for proc in PROCESSES:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def main():
    missing = [str(p) for p in (RELAY, CLIENT) if not p.exists()]
    if missing:
        raise SystemExit(f"Missing binaries: {missing}; run cargo build --release first")

    base_env = os.environ.copy()
    with tempfile.TemporaryDirectory(prefix="osiris-relay-identity-") as tmp:
        tmp = Path(tmp)
        relay_env = base_env | {
            "OSIRIS_RELAY_SERVER_ENABLED": "true",
            "OSIRIS_RELAY_LISTEN_ADDR": "/ip4/127.0.0.1/tcp/4017",
            "OSIRIS_RELAY_NODE_KEY": str(tmp / "relay.key"),
        }
        relay_proc = Process("relay", [str(RELAY)], relay_env)
        try:
            listen_line = relay_proc.wait_for("relay_listening=", timeout=20)
            relay_addr = listen_line.split("=", 1)[1]
            relay_peer_id = relay_addr.rsplit("/p2p/", 1)[1]
            relay_multiaddr = f"/ip4/127.0.0.1/tcp/4017/p2p/{relay_peer_id}"

            receiver_key = tmp / "receiver.key"
            receiver_env = base_env | {
                "OSIRIS_RELAY_CLIENT_ROLE": "receiver",
                "OSIRIS_RELAY_CLIENT_KEY": str(receiver_key),
                "OSIRIS_RELAY_MULTIADDR": relay_multiaddr,
            }
            receiver = Process("receiver-first-boot", [str(CLIENT)], receiver_env)
            receiver_peer_line = receiver.wait_for("peer_id=", timeout=10)
            receiver_peer_id = receiver_peer_line.split("=", 1)[1]
            receiver.wait_for("relay_reservation_accepted=true", timeout=25)
            receiver.wait_for("relay_reserved_address=", timeout=10)

            # Restart the same logical node with the same key file. Its peer ID
            # must remain stable, and it must obtain a fresh relay reservation.
            receiver.proc.terminate()
            receiver.proc.wait(timeout=5)
            relay_proc.wait_for(f"relay_peer_disconnected={receiver_peer_id}", timeout=10)

            receiver = Process("receiver-restart", [str(CLIENT)], receiver_env)
            restarted_peer_line = receiver.wait_for("peer_id=", timeout=10)
            restarted_peer_id = restarted_peer_line.split("=", 1)[1]
            if restarted_peer_id != receiver_peer_id:
                raise AssertionError(
                    f"peer identity changed across restart: {receiver_peer_id} -> {restarted_peer_id}"
                )
            receiver.wait_for("relay_reservation_accepted=true", timeout=25)
            receiver.wait_for("relay_reserved_address=", timeout=10)
            print("RELAY_CLIENT_IDENTITY_PERSISTENCE=PASS", flush=True)

            sender_env = base_env | {
                "OSIRIS_RELAY_CLIENT_ROLE": "sender",
                "OSIRIS_RELAY_CLIENT_KEY": str(tmp / "sender.key"),
                "OSIRIS_RELAY_MULTIADDR": relay_multiaddr,
                "OSIRIS_RELAY_TARGET_PEER": receiver_peer_id,
            }
            sender = Process("sender", [str(CLIENT)], sender_env)
            sender.wait_for("peer_connected=", timeout=15)
            sender.wait_for("relay_observation_published=true", timeout=25)
            receiver.wait_for("relay_observation_received", timeout=25)
            if not any("relayed=true" in line for line in sender.history):
                sender.wait_for("relayed=true", timeout=10)
            print("THREE_PROCESS_CIRCUIT_RELAY_TEST=PASS", flush=True)
        finally:
            stop_all()


if __name__ == "__main__":
    main()
