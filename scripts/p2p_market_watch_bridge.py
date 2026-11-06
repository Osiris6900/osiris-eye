#!/usr/bin/env python3
"""Spawn an OSIRIS P2P node and autonomously broadcast market observations.

Closes the loop for a self-watching mesh: the node process reads signed
observation JSON lines from its stdin (its existing ingestion path) and
gossips them; this bridge feeds it the global market watch output on an
interval, 24h, with no per-cycle operator action.

Requires:
  - built release binary (cargo build --release --manifest-path services/p2p-node/Cargo.toml)
  - OSIRIS_MARKET_WATCH_BROADCAST=1 (bridge kill switch)
Optional env:
  - OSIRIS_WATCH_INTERVAL (seconds, 15..3600, default 60)
  - OSIRIS_WATCH_CYCLES (default 1440 = one cycle/minute for 24h)
  - OSIRIS_WATCH_STATE_DIR (node key/state files; default tempdir)
  - OSIRIS_BOOTSTRAP_PEERS / OSIRIS_LISTEN_ADDR / OSIRIS_E2EE_ENABLED pass through
  - OSIRIS_P2P_INGEST_ENABLED=1 + OSIRIS_DB_PATH to also store locally
"""
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "quant-api" / "src"))

from osiris_api.data_store import ResearchStore  # noqa: E402
from osiris_api.market_watch_bridge import run_bridge  # noqa: E402

BINARY = ROOT / "target" / "release" / "osiris-p2p-node"
if not BINARY.exists():
    BINARY = ROOT / "services" / "p2p-node" / "target" / "release" / "osiris-p2p-node"


def main() -> int:
    if os.getenv("OSIRIS_MARKET_WATCH_BROADCAST") != "1":
        print("refused: set OSIRIS_MARKET_WATCH_BROADCAST=1 to run the autonomous bridge",
              file=sys.stderr)
        return 2
    if not BINARY.exists():
        print("P2P binary not found; build it before running this bridge", file=sys.stderr)
        return 2

    interval = float(os.getenv("OSIRIS_WATCH_INTERVAL", "60"))
    cycles = int(os.getenv("OSIRIS_WATCH_CYCLES", "1440"))

    state = Path(os.getenv("OSIRIS_WATCH_STATE_DIR") or tempfile.mkdtemp(prefix="osiris-watch-"))
    state.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("OSIRIS_NODE_KEY", str(state / "node.identity"))
    env.setdefault("OSIRIS_TRANSPORT_KEY", str(state / "node.transport"))
    env.setdefault("OSIRIS_REPLAY_CACHE", str(state / "node.replay"))
    env.setdefault("OSIRIS_PEER_KEY_STORE", str(state / "node.peer-keys.sqlite3"))
    env.setdefault("OSIRIS_HEALTH_HTTP_ADDR", "127.0.0.1:0")

    proc = subprocess.Popen(
        [str(BINARY)], cwd=ROOT, env=env, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )

    def pump() -> None:
        for line in iter(proc.stdout.readline, ""):
            print(f"[node] {line.rstrip()}", flush=True)

    threading.Thread(target=pump, daemon=True).start()

    store = None
    if os.getenv("OSIRIS_P2P_INGEST_ENABLED") == "1":
        store = ResearchStore()

    def write_line(line: str) -> None:
        if proc.poll() is not None:
            raise RuntimeError("node process exited during bridge run")
        proc.stdin.write(line + "\n")
        proc.stdin.flush()

    try:
        result = run_bridge(
            write_line, store=store,
            interval_seconds=interval, max_cycles=cycles,
        )
        print(f"bridge finished: {result['cycles']} cycles at "
              f"{result['interval_seconds']}s intervals", flush=True)
        return 0
    finally:
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


if __name__ == "__main__":
    sys.exit(main())
