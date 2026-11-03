#!/usr/bin/env python3
"""Three independent OSIRIS node processes converge on an authority-signed revocation."""
import os, queue, subprocess, tempfile, threading, time, json
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
        self.proc = subprocess.Popen([str(BINARY)], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        PROCESSES.append(self.proc)
        threading.Thread(target=self._pump, daemon=True).start()
    def _pump(self):
        for raw in self.proc.stdout:
            line = raw.rstrip()
            self.history.append(line)
            print(f"[{self.name}] {line}", flush=True)
            self.lines.put(line)
    def wait(self, needle, timeout=30):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if any(needle in line for line in self.history):
                return
            if self.proc.poll() is not None:
                raise RuntimeError(f"{self.name} exited {self.proc.returncode}: {self.history}")
            try: self.lines.get(timeout=.2)
            except queue.Empty: pass
        raise TimeoutError(f"{self.name} missing {needle}; logs={self.history}")

def stop_all():
    for p in PROCESSES:
        if p.poll() is None: p.terminate()
    for p in PROCESSES:
        try: p.wait(timeout=5)
        except subprocess.TimeoutExpired: p.kill()

def main():
    if not BINARY.exists(): raise SystemExit(f"missing {BINARY}; build release node first")
    with tempfile.TemporaryDirectory(prefix="osiris-revocation-mesh-") as td:
        td = Path(td)
        fixture = subprocess.run([CARGO, "run", "--quiet", "--manifest-path",
            str(ROOT / "services/p2p-node/Cargo.toml"), "--example", "make_revocation_fixture", "--",
            str(td / "authority.json")], cwd=ROOT, text=True, capture_output=True, check=True)
        values = dict(line.split("=", 1) for line in fixture.stdout.splitlines() if "=" in line)
        authority, target = values["authority_public_key"], values["target_peer_id"]
        base = os.environ.copy()
        def env_for(name, bootstrap=None, seeded=False):
            e = base.copy()
            e.update({"OSIRIS_NODE_KEY": str(td / f"{name}.key"),
                "OSIRIS_LISTEN_ADDR": "/ip4/127.0.0.1/tcp/0",
                "OSIRIS_REVOCATION_AUTHORITY_PUBLIC_KEY": authority,
                "OSIRIS_REVOCATION_REGISTRY": str(td / f"{name}.json"),
                "OSIRIS_REPLAY_CACHE": str(td / f"{name}.cache")})
            if bootstrap: e["OSIRIS_BOOTSTRAP_PEERS"] = bootstrap
            if seeded: e["OSIRIS_REVOCATION_REGISTRY"] = str(td / "authority.json")
            return e
        a = Node("authority-seed", env_for("a", seeded=True))
        try:
            a.wait("listener_ready ")
            addr_a = next(x for x in a.history if "listener_ready " in x).split(" address=",1)[1].split()[0]
            b = Node("peer-b", env_for("b", bootstrap=addr_a))
            b.wait("listener_ready ")
            addr_b = next(x for x in b.history if "listener_ready " in x).split(" address=",1)[1].split()[0]
            c = Node("peer-c", env_for("c", bootstrap=addr_b))
            a.wait(f"signed_revocation_registry=loaded records=1", 10)
            b.wait("revocation_sync_snapshot_applied", 35)
            b.wait("applied=1", 35)
            c.wait("revocation_sync_snapshot_applied", 35)
            c.wait("applied=1", 35)
            for name in ("b", "c"):
                persisted = json.loads((td / f"{name}.json").read_text())
                assert any(row["peer_id"] == target for row in persisted), f"{name} did not persist revocation"
            print("THREE_NODE_SIGNED_REVOCATION_SYNC=PASS", flush=True)
        finally:
            stop_all()

if __name__ == "__main__":
    try: main()
    finally: stop_all()
