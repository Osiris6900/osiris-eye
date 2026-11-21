#!/usr/bin/env python3
"""Run a real four-node Linux soak and reject leaked or orphaned descendants.

The test is a child subreaper so a regression is observed and cleaned up here,
rather than leaving unreapable zombies under a container's PID 1. Adoption or
test-owned cleanup still fails the test. This short, local process regression
never constitutes independent-host capacity or release acceptance.
"""
from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

from check_p2p_load_acceptance import POLICY_PATH, check as check_release_evidence
from verify_all import source_snapshot

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "scripts/run_p2p_soak.py"
TIMEOUT_SECONDS = 180


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeError(reason)


def find_binary(requested: Path | None) -> Path:
    configured = requested or os.getenv("OSIRIS_P2P_TEST_BINARY")
    candidates = [Path(configured)] if configured else [
        ROOT / "services/p2p-node/target/release/osiris-p2p-node",
        ROOT / "services/p2p-node/target/debug/osiris-p2p-node",
        ROOT / "target/release/osiris-p2p-node",
        ROOT / "target/debug/osiris-p2p-node",
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve(strict=True)
    raise RuntimeError("explicit_or_built_p2p_binary_required")


@dataclass(frozen=True)
class Identity:
    pid: int
    started: int


@dataclass(frozen=True)
class Process:
    identity: Identity
    parent: int
    state: str


def process(pid: int) -> Process | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return Process(Identity(pid, int(fields[19])), int(fields[1]), fields[0])


def snapshot() -> dict[int, Process]:
    result = {}
    for entry in Path("/proc").iterdir():
        if entry.name.isdecimal():
            value = process(int(entry.name))
            if value is not None:
                result[value.identity.pid] = value
    return result


class Subreaper:
    def __enter__(self):
        require(sys.platform == "linux" and Path("/proc/self/stat").is_file(),
                "linux_proc_and_subreaper_required")
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.prctl.restype = ctypes.c_int
        previous = ctypes.c_int()
        require(self.libc.prctl(37, ctypes.byref(previous), 0, 0, 0) == 0,
                "cannot_read_child_subreaper_state")
        self.previous = previous.value
        require(self.libc.prctl(36, 1, 0, 0, 0) == 0,
                "cannot_enable_child_subreaper")
        return self

    def __exit__(self, *_):
        require(self.libc.prctl(36, self.previous, 0, 0, 0) == 0,
                "cannot_restore_child_subreaper_state")


class Descendants:
    def __init__(self, controller: subprocess.Popen, baseline: dict[int, Process]):
        self.controller = controller
        value = process(controller.pid)
        require(value is not None, "controller_exited_before_tracking")
        self.root = value.identity
        self.owned = {self.root}
        self.baseline = {p.identity for p in baseline.values() if p.parent == os.getpid()}
        self.adopted: set[Identity] = set()
        self.reaped: set[Identity] = set()

    def observe(self) -> dict[int, Process]:
        current = snapshot()
        # A child orphaned before its first sample is still identifiable when
        # it is adopted by this dedicated subreaper process.
        for value in current.values():
            if (value.parent == os.getpid() and value.identity != self.root
                    and value.identity not in self.baseline):
                self.owned.add(value.identity)
                self.adopted.add(value.identity)
        changed = True
        while changed:
            parents = {p.identity.pid for p in current.values() if p.identity in self.owned}
            found = {p.identity for p in current.values() if p.parent in parents}
            changed = not found.issubset(self.owned)
            self.owned.update(found)
        return current

    def remaining(self, current: dict[int, Process]) -> list[Process]:
        return [p for p in current.values() if p.identity in self.owned
                and p.identity != self.root]

    def reap(self, current: dict[int, Process]) -> None:
        for value in current.values():
            if (value.identity in self.adopted and value.parent == os.getpid()
                    and value.state == "Z"):
                # Verify identity again before touching this PID. Never reap
                # the Popen-owned controller or an unrelated baseline child.
                fresh = process(value.identity.pid)
                if fresh is not None and fresh.identity == value.identity:
                    try:
                        pid, _ = os.waitpid(value.identity.pid, os.WNOHANG)
                    except ChildProcessError:
                        continue
                    if pid:
                        self.reaped.add(value.identity)

    def signal(self, signum: int) -> None:
        for identity in tuple(self.owned):
            fresh = process(identity.pid)
            if fresh is not None and fresh.identity == identity and fresh.state != "Z":
                try:
                    os.kill(identity.pid, signum)
                except ProcessLookupError:
                    pass

    def cleanup(self) -> list[Process]:
        # A failing collector gets a bounded graceful interval, then only
        # verified PID/start-time descendants are killed. No killpg or PID-name
        # matching can affect another concurrent test or user process.
        current = self.observe()
        self.reap(current)
        self.controller.poll()
        if self.controller.returncode is None or self.remaining(current):
            for signum, seconds in ((signal.SIGTERM, 8), (signal.SIGKILL, 5)):
                self.signal(signum)
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    current = self.observe()
                    self.reap(current)
                    self.controller.poll()
                    if self.controller.returncode is not None and not self.remaining(self.observe()):
                        return []
                    time.sleep(.02)
                # Include descendants adopted or discovered during cleanup.
                current = self.observe()
            return self.remaining(current)
        return []


def reserved_port() -> int:
    # Keep the OS reservation until immediately before Popen. The collector
    # cannot inherit a listener, so any subsequent bind race fails this test.
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


def agent_finish_contract(binary: Path, directory: Path) -> dict:
    """At the actual Agent finished event, every node must already be reaped.

    Post-controller absence alone can miss premature finished announcements in
    small runs that happen to complete before the controller's fallback timer.
    """
    events = queue.Queue(maxsize=512)
    invalid_stream = threading.Event()
    reader_done = threading.Event()
    tracked_nodes = set()
    baseline = snapshot()
    port = reserved_port()
    with (directory / "agent-stderr.log").open("xb") as output:
        controller = subprocess.Popen([
            sys.executable, str(COLLECTOR), "--agent", "--binary", str(binary),
            "--count", "4", "--port", str(port), "--node-worker-threads", "1",
        ], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=output,
            text=True, bufsize=1)
        descendants = Descendants(controller, baseline)

        def pump():
            try:
                while True:
                    line = controller.stdout.readline(65537)
                    if not line:
                        return
                    require(len(line) <= 65536, "agent_event_too_large")
                    event = json.loads(line)
                    require(isinstance(event, dict), "agent_event_not_object")
                    if event.get("kind") == "finished":
                        at_finished = snapshot()
                        event["children_at_finished"] = sum(
                            p.parent == controller.pid for p in at_finished.values())
                        event["tracked_nodes_at_finished"] = sum(
                            p.identity in tracked_nodes for p in at_finished.values())
                    events.put_nowait(event)
            except (OSError, ValueError, RuntimeError, queue.Full):
                invalid_stream.set()
            finally:
                reader_done.set()

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()

        def command(**value):
            controller.stdin.write(json.dumps(value, separators=(",", ":")) + "\n")
            controller.stdin.flush()

        peers = {}
        started = listener = peers_requested = finish_requested = finished = False
        errors = []
        deadline = time.monotonic() + 90
        try:
            while True:
                current = descendants.observe()
                descendants.reap(current)
                try:
                    event = events.get(timeout=.02)
                except queue.Empty:
                    event = {}
                kind = event.get("kind")
                if kind == "metadata":
                    command(action="sink")
                elif kind == "peer":
                    peers[event["node"]] = event["peer"]
                elif kind == "listener":
                    listener = True
                elif kind == "started":
                    started = True
                elif kind == "fatal":
                    errors.append("agent_protocol_reported_failure")
                    break
                elif kind == "finished":
                    finished = True
                    # The parent must have reaped its direct children, rather
                    # than merely signaled them or left zombies to be adopted.
                    if (not finish_requested or event["children_at_finished"]
                            or event["tracked_nodes_at_finished"]):
                        errors.append("agent_finished_before_node_reap")
                    controller.stdin.close()
                if 0 in peers and listener and not peers_requested:
                    command(action="peers", bootstrap=f"/ip4/127.0.0.1/tcp/{port}/p2p/{peers[0]}",
                            recipient=peers[0])
                    peers_requested = True
                if started and len(peers) == 4 and not finish_requested:
                    current = descendants.observe()
                    tracked_nodes = {p.identity for p in current.values()
                                     if p.parent == controller.pid}
                    require(len(tracked_nodes) == 4, "four_actual_agent_node_children_required")
                    command(action="finish")
                    finish_requested = True
                if invalid_stream.is_set():
                    errors.append("agent_protocol_stream_invalid")
                    break
                if controller.poll() is not None and reader_done.is_set() and events.empty():
                    break
                if time.monotonic() >= deadline:
                    errors.append("bounded_agent_protocol_timeout")
                    break
        finally:
            remaining = descendants.cleanup()
            if controller.stdin and not controller.stdin.closed:
                controller.stdin.close()
            reader.join(timeout=2)
            if not reader.is_alive():
                controller.stdout.close()
        if not finished:
            errors.append("agent_finished_event_missing")
        if controller.returncode != 0:
            errors.append("agent_protocol_subprocess_failed")
        if descendants.adopted:
            errors.append("agent_protocol_orphan_adoption")
        if remaining:
            errors.append("agent_protocol_cleanup_incomplete")
        if reader.is_alive():
            errors.append("agent_protocol_reader_not_joined")
        return {"passed": not errors, "errors": errors, "node_identities_observed": len(tracked_nodes),
                "finished_after_reap": finished and "agent_finished_before_node_reap" not in errors,
                "orphan_adoptions": len(descendants.adopted), "reaped_orphans": len(descendants.reaped),
                "remaining_owned_processes": len(remaining)}


def parsed_report(path: Path, before: dict, binary_sha256: str) -> dict:
    require(path.is_file() and 0 < path.stat().st_size <= 4 * 1024 * 1024,
            "bounded_original_soak_report_required")
    report = json.loads(path.read_bytes())
    require(isinstance(report, dict) and report.get("schema") == "osiris.p2p_load_acceptance.v1",
            "soak_report_parser_failed")
    require(report.get("measurement_class") == "single_host_process_regression",
            "local_measurement_class_required")
    require(report.get("commit_sha") == before["git_head"]
            and report.get("clean_worktree") is (not before["dirty"]),
            "soak_report_source_binding_mismatch")
    workload, delivery = report.get("workload"), report.get("delivery")
    require(isinstance(workload, dict) and workload.get("peers") == 4
            and workload.get("messages") == 30 and workload.get("duration_seconds", 0) >= 20,
            "four_peer_thirty_message_soak_required")
    require(isinstance(delivery, dict)
            and all(type(delivery.get(name)) is int and delivery[name] == expected
                    for name, expected in (("sent", 30), ("delivered", 30), ("lost", 0),
                                           ("rejected", 0), ("pending", 0),
                                           ("duplicate_deliveries", 0))),
            "exact_soak_delivery_accounting_failed")
    records = report.get("delivery_records")
    require(isinstance(records, list) and len(records) == 30
            and all(isinstance(r, dict) and r.get("outcome") == "delivered"
                    and r.get("delivery_count") == 1 and r.get("duplicate_deliveries") == 0
                    and isinstance(r.get("payload_sha256"), str)
                    and re.fullmatch(r"[0-9a-f]{64}", r["payload_sha256"]) for r in records)
            and len({r["payload_sha256"] for r in records}) == 30,
            "soak_per_message_records_failed")
    hosts, notes, runtime = report.get("hosts"), report.get("measurement_notes"), report.get("runtime_config")
    require(isinstance(hosts, list) and len(hosts) == 2
            and all(h.get("binary_sha256") == binary_sha256 for h in hosts),
            "soak_report_binary_binding_mismatch")
    require(isinstance(runtime, dict) and runtime.get("node_worker_threads_requested") == 1
            and runtime.get("observed_runtime_worker_count_verified") is False,
            "one_requested_node_worker_required")
    require(isinstance(notes, dict)
            and notes.get("traffic_mode") == "drained_restart"
            and notes.get("workload_retransmissions") == 0
            and notes.get("independent_host_fingerprints_distinct") is False,
            "local_paused_no_resend_scope_required")
    for name in ("independent_host_acceptance", "release_gate_accepted", "live_trading_authorized"):
        require(name not in report or report[name] is False, "unearned_acceptance_claim")
    try:
        check_release_evidence(report, json.loads(POLICY_PATH.read_bytes()))
    except ValueError as error:
        require(str(error) == "only operator-attested real multi-host evidence is accepted",
                "unexpected_release_gate_parser_failure")
    else:
        raise RuntimeError("local_soak_must_not_satisfy_release_gate")
    return report


def run(binary: Path, artifacts: Path | None) -> dict:
    result = {
        "schema": "osiris.local-soak-cleanup-regression.v1", "passed": False,
        "independent_host_acceptance": False, "release_gate_accepted": False,
        "live_trading_authorized": False,
    }
    before = source_snapshot()
    binary_sha256 = hashlib.sha256(binary.read_bytes()).hexdigest()
    errors = []
    with tempfile.TemporaryDirectory(prefix="osiris-soak-cleanup-") as temporary, Subreaper():
        directory = Path(temporary)
        contract = agent_finish_contract(binary, directory)
        result["agent_finish_contract"] = contract
        errors.extend(contract["errors"])
        report_path = directory / "soak.json"
        baseline = snapshot()
        with (directory / "controller.log").open("xb") as output:
            controller = subprocess.Popen([
                sys.executable, str(COLLECTOR), "--binary", str(binary), "--local-agents",
                "--peers", "4", "--messages", "30", "--duration", "20",
                "--node-worker-threads", "1", "--port", str(reserved_port()),
                "--evidence", str(report_path),
            ], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=output)
            descendants = Descendants(controller, baseline)
            deadline = time.monotonic() + TIMEOUT_SECONDS
            try:
                while True:
                    current = descendants.observe()
                    descendants.reap(current)
                    if controller.poll() is not None:
                        at_exit = descendants.remaining(descendants.observe())
                        result["descendants_at_controller_exit"] = len(at_exit)
                        result["zombies_at_controller_exit"] = sum(p.state == "Z" for p in at_exit)
                        if at_exit:
                            errors.append("descendants_survived_controller")
                        break
                    if time.monotonic() >= deadline:
                        errors.append("bounded_soak_timeout")
                        break
                    time.sleep(.02)
            finally:
                remaining = descendants.cleanup()
            result.update(
                controller_exit_code=controller.returncode,
                observed_descendants=len(descendants.owned) - 1,
                orphan_adoptions=len(descendants.adopted), reaped_orphans=len(descendants.reaped),
                remaining_owned_processes=len(remaining),
            )
            if controller.returncode != 0:
                errors.append("collector_subprocess_failed")
            if descendants.adopted:
                errors.append("unexpected_orphan_adoption")
            if remaining:
                errors.append("owned_process_cleanup_incomplete")
            if len(descendants.owned) - 1 < 6:
                errors.append("actual_agent_and_node_descendants_not_observed")
        after = source_snapshot()
        unchanged = before == after and hashlib.sha256(binary.read_bytes()).hexdigest() == binary_sha256
        result.update(source_unchanged=unchanged, commit_sha=before["git_head"],
                      worktree_sha256=before["worktree_sha256"], binary_sha256=binary_sha256)
        if not unchanged:
            errors.append("source_or_binary_changed")
        try:
            report = parsed_report(report_path, before, binary_sha256)
            result.update(parser_success=True, measurement_class=report["measurement_class"],
                          peers=4, sent=30, delivered=30, lost=0,
                          original_report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest())
        except (OSError, ValueError, TypeError, KeyError, RecursionError, RuntimeError) as error:
            result["parser_success"] = False
            # Reasons produced by this script are fixed metadata, never node
            # logs, exception payloads, endpoints or key material.
            errors.append(str(error) if type(error) is RuntimeError else "original_report_parse_failed")
        result.update(passed=not errors, errors=sorted(set(errors)))
        if artifacts is not None:
            artifacts = artifacts.resolve()
            require(not artifacts.is_relative_to(ROOT), "artifacts_must_be_outside_source")
            artifacts.mkdir(mode=0o700, parents=True, exist_ok=False)
            if report_path.is_file():
                (artifacts / "soak.json").write_bytes(report_path.read_bytes())
            (artifacts / "cleanup-verdict.json").write_text(json.dumps(result, sort_keys=True) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--artifacts", type=Path, help="exclusive output directory outside checkout")
    args = parser.parse_args()
    try:
        result = run(find_binary(args.binary), args.artifacts)
    except (OSError, ValueError, TypeError, KeyError, RecursionError, RuntimeError):
        result = {"schema": "osiris.local-soak-cleanup-regression.v1", "passed": False,
                  "errors": ["linux_soak_cleanup_regression_could_not_complete"],
                  "independent_host_acceptance": False, "release_gate_accepted": False,
                  "live_trading_authorized": False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
