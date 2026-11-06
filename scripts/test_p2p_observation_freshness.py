#!/usr/bin/env python3
"""Exercise timestamp admission in the actual core node, including E2EE receive.

The permissive research sender deliberately publishes old/future event times;
the recipient enables a 300-second freshness window.  Rejection must happen
after authenticated decryption and must not stop the node from accepting a fresh
observation.  This is a localhost acceptance test, not cross-host evidence.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def find_binary(requested: Path | None) -> Path:
    candidates = [requested] if requested else [
        ROOT / "target/release/osiris-p2p-node",
        ROOT / "services/p2p-node/target/release/osiris-p2p-node",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("Build the core node first or pass --binary")


def node_env(directory: Path) -> dict[str, str]:
    # Tests must not inherit operator identities, recipients, trust or rotation.
    env = {key: value for key, value in os.environ.items() if not key.startswith("OSIRIS_")}
    env.update(
        OSIRIS_NODE_KEY=str(directory / "identity"),
        OSIRIS_TRANSPORT_KEY=str(directory / "transport"),
        OSIRIS_PEER_KEY_STORE=str(directory / "peer-keys.sqlite3"),
        OSIRIS_REPLAY_CACHE=str(directory / "replay"),
        OSIRIS_LISTEN_ADDR="/ip4/127.0.0.1/tcp/0",
        OSIRIS_HEALTH_HTTP_ADDR="127.0.0.1:0",
        OSIRIS_MDNS_ENABLED="0",
        OSIRIS_E2EE_ENABLED="1",
    )
    return env


class Node:
    def __init__(self, name: str, binary: Path, env: dict[str, str]):
        self.name = name
        self.proc = subprocess.Popen(
            [str(binary)], cwd=ROOT, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        self.condition = threading.Condition()
        self.lines: list[str] = []
        self.overflow = False
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            line = raw.rstrip()
            with self.condition:
                if len(self.lines) >= 4000 or len(line) > 16_384:
                    self.overflow = True
                else:
                    self.lines.append(line)
                self.condition.notify_all()
        with self.condition:
            self.condition.notify_all()

    def checkpoint(self) -> int:
        with self.condition:
            return len(self.lines)

    def wait(self, predicate, description: str, *, after: int = 0, timeout: float = 25) -> str:
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                if self.overflow:
                    raise RuntimeError(f"{self.name}: test log capacity exceeded")
                for line in self.lines[after:]:
                    if predicate(line):
                        return line
                if self.proc.poll() is not None:
                    raise RuntimeError(f"{self.name} exited before {description}: {self.lines[-10:]}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"{self.name}: {description}: {self.lines[-10:]}")
                self.condition.wait(min(remaining, 0.1))

    def send(self, observation: dict) -> int:
        after = self.checkpoint()
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(observation, separators=(",", ":")) + "\n")
        self.proc.stdin.flush()
        return after

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        self.reader.join(timeout=2)
        for stream in (self.proc.stdin, self.proc.stdout):
            if stream is not None:
                stream.close()


def timestamp(offset: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).strftime("%Y-%m-%dT%H:%M:%SZ")


def observation(observed_at: str) -> dict:
    payload = "freshness-acceptance-" + uuid.uuid4().hex
    return {
        "schema": "osiris.observation.v1", "instrument": "FRESH-" + uuid.uuid4().hex[:12],
        "observed_at": observed_at, "provider": "local-freshness-acceptance",
        "payload": payload, "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
    }


def startup_rejected(binary: Path, directory: Path, setting: dict, reason: str) -> None:
    env = node_env(directory)
    env.update(setting)
    result = subprocess.run(
        [str(binary)], cwd=ROOT, env=env, input="", capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=10,
    )
    if result.returncode == 0 or reason not in result.stdout + result.stderr:
        raise AssertionError(f"invalid configuration was not rejected with {reason}")


def run(binary: Path) -> dict:
    nodes: list[Node] = []
    checks: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="osiris-freshness-") as raw:
            root = Path(raw)
            for name in ("strict", "sender", "recipient", "invalid-age", "invalid-trust"):
                (root / name).mkdir()
            startup_rejected(binary, root / "invalid-age",
                             {"OSIRIS_MAX_OBSERVATION_AGE_SECS": "0"},
                             "invalid_observation_max_age_seconds")
            checks.append("invalid_freshness_configuration_fails_closed")
            if os.name == "posix":
                startup_rejected(binary, root / "invalid-trust",
                                 {"OSIRIS_TRUSTED_PEERS": os.fsdecode(b"\xff")},
                                 "must contain valid UTF-8 peer IDs")
                checks.append("non_utf8_allowlist_fails_closed")

            env = node_env(root / "strict")
            env["OSIRIS_MAX_OBSERVATION_AGE_SECS"] = "300"
            strict = Node("strict", binary, env)
            nodes.append(strict)
            strict.wait(lambda line: line.startswith("listener_ready "), "strict listener")
            for event_time, reason in ((timestamp(-3600), "stale_observation"),
                                       (timestamp(3600), "observation_from_future"),
                                       ("2025-02-29T00:00:00Z", "invalid_observed_at")):
                mark = strict.send(observation(event_time))
                strict.wait(lambda line: line == f"publish_rejected reason={reason}",
                            reason, after=mark)
            # With no recipients configured this specific error proves fresh
            # input passed time validation and reached the E2EE publishing gate.
            mark = strict.send(observation(timestamp()))
            strict.wait(lambda line: line == "publish_rejected reason=e2ee_recipient_count_out_of_range",
                        "fresh input advances to recipient gate", after=mark)
            checks.append("local_stale_future_and_impossible_dates_rejected_without_exit")
            strict.stop()

            recipients = root / "recipients.txt"
            recipients.write_text("")
            env = node_env(root / "sender")
            env["OSIRIS_E2EE_RECIPIENTS_FILE"] = str(recipients)
            sender = Node("sender", binary, env)
            nodes.append(sender)
            endpoint = sender.wait(lambda line: line.startswith("listener_ready ") and " address=" in line,
                                   "sender endpoint").split(" address=", 1)[1].split()[0]
            sender_peer = endpoint.rsplit("/p2p/", 1)[1]
            env = node_env(root / "recipient")
            env.update(OSIRIS_BOOTSTRAP_PEERS=endpoint, OSIRIS_MAX_OBSERVATION_AGE_SECS="300")
            recipient = Node("recipient", binary, env)
            nodes.append(recipient)
            recipient_peer = recipient.wait(lambda line: line.startswith("peer_id="),
                                            "recipient identity").split("=", 1)[1]
            sender.wait(lambda line: f"key_binding_accepted peer={recipient_peer} " in line,
                        "sender stores recipient signed binding")
            recipient.wait(lambda line: f"key_binding_accepted peer={sender_peer} " in line,
                           "recipient stores sender signed binding")
            recipients.write_text(recipient_peer + "\n")
            for event_time in (timestamp(-3600), timestamp(3600)):
                mark = recipient.checkpoint()
                sender.send(observation(event_time))
                recipient.wait(lambda line: "reason=invalid_plaintext_observation" in line,
                               "freshness recipient rejects decrypted out-of-window event", after=mark)
            checks.append("recipient_rejects_authenticated_encrypted_stale_and_future_events")
            fresh = observation(timestamp())
            sender.send(fresh)
            recipient.wait(lambda line: "encrypted_observation_received" in line
                           and f"instrument={fresh['instrument']} " in line
                           and f"payload_sha256={fresh['payload_sha256']} " in line,
                           "recipient accepts exact fresh decrypted observation")
            checks.append("recipient_accepts_exact_fresh_event_after_rejections")
            return {"status": "PASS", "scope": "local_process_acceptance",
                    "checks": checks, "binary": str(binary)}
    finally:
        for node in reversed(nodes):
            node.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path)
    args = parser.parse_args()
    result = run(find_binary(args.binary))
    print("P2P_OBSERVATION_FRESHNESS=PASS")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, OSError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
        print(f"P2P_OBSERVATION_FRESHNESS=FAIL: {error}", flush=True)
        raise SystemExit(1)
