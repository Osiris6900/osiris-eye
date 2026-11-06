#!/usr/bin/env python3
"""Deterministic in-process P2P fault-injection and recovery matrix (issue #91).

Simulates mesh behavior under faults — abrupt disconnects, bootstrap and
relay outage with failover, reconnect backoff, partial partitions with heal
sync, duplicate delivery, stale-peer eviction and revoked-peer fail-closed —
and asserts bounded resource use throughout. Simulated process-local
evidence only; not a substitute for multi-host testing.
"""
from __future__ import annotations

import json
import sys
from collections import deque

MAX_INBOX = 64
MAX_DEDUP = 256
MAX_BACKOFF_SECONDS = 8.0
MAX_RECONNECT_ATTEMPTS = 5
STALE_AFTER_ROUNDS = 3


class SimNode:
    """Minimal peer model with bounded queues and bounded dedup state."""

    def __init__(self, peer_id: str):
        self.peer_id = peer_id
        self.connected: set[str] = set()
        self.down = False
        self.inbox: deque[str] = deque(maxlen=MAX_INBOX)
        self.dedup: set[str] = set()
        self.dedup_order: deque[str] = deque()
        self.log: deque[str] = deque(maxlen=MAX_INBOX)
        self.duplicates_suppressed = 0
        self.dropped_overflow = 0
        self.rejected_connections = 0
        self.last_seen_round = 0

    def remember(self, message_id: str) -> None:
        if message_id in self.dedup:
            return
        if len(self.dedup) >= MAX_DEDUP:
            oldest = self.dedup_order.popleft()
            self.dedup.discard(oldest)
        self.dedup.add(message_id)
        self.dedup_order.append(message_id)


class FaultMesh:
    """Deterministic mesh model with revocation-aware, fail-closed delivery."""

    def __init__(self, peer_ids: list[str], relays: tuple[str, ...] = ("relay-1", "relay-2")):
        self.nodes = {pid: SimNode(pid) for pid in peer_ids}
        self.round = 0
        self.relays = {relay: True for relay in relays}
        self.bootstrap_up = True
        self.revocations: set[str] = set()
        self.peer_cache = {pid: set(peer_ids) - {pid} for pid in peer_ids}

    def connect(self, a: str, b: str) -> bool:
        na, nb = self.nodes[a], self.nodes[b]
        if a in self.revocations or b in self.revocations:
            if a in self.revocations:
                na.rejected_connections += 1
            else:
                nb.rejected_connections += 1
            return False
        if na.down or nb.down:
            return False
        na.connected.add(b)
        nb.connected.add(a)
        return True

    def deliver(self, src: str, dst: str, message_id: str) -> bool:
        ns, nd = self.nodes[src], self.nodes[dst]
        if src in self.revocations or ns.down or nd.down:
            return False
        if dst not in ns.connected:
            return False
        if message_id in nd.dedup:
            nd.duplicates_suppressed += 1
            return True  # duplicate suppressed; counts as handled, not stored
        if len(nd.inbox) >= MAX_INBOX:
            nd.dropped_overflow += 1
            return False
        nd.remember(message_id)
        nd.inbox.append(message_id)
        nd.log.append(message_id)
        return True

    def broadcast(self, src: str, message_id: int) -> int:
        source = self.nodes[src]
        if message_id not in source.dedup:
            source.remember(message_id)
            source.log.append(message_id)
        delivered = 0
        for dst in sorted(source.connected):
            if self.deliver(src, dst, message_id):
                delivered += 1
        return delivered

    def keepalive_tick(self) -> None:
        self.round += 1
        for node in self.nodes.values():
            if not node.down:
                node.last_seen_round = self.round

    def evict_stale(self) -> list[str]:
        evicted: list[str] = []
        for pid, node in self.nodes.items():
            for peer in sorted(self.peer_cache[pid]):
                peer_node = self.nodes[peer]
                if peer_node.down and self.round - peer_node.last_seen_round > STALE_AFTER_ROUNDS:
                    self.peer_cache[pid].discard(peer)
                    node.connected.discard(peer)
                    if peer not in evicted:
                        evicted.append(peer)
        return sorted(evicted)

    def resource_bounds_ok(self) -> bool:
        return all(
            len(node.inbox) <= MAX_INBOX and len(node.dedup) <= MAX_DEDUP
            for node in self.nodes.values()
        )


def backoff_schedule(attempts: int, base: float = 1.0, cap: float = MAX_BACKOFF_SECONDS) -> list[float]:
    return [min(cap, base * (2 ** i)) for i in range(attempts)]


def reconnect_with_backoff(mesh: FaultMesh, node_id: str, target: str,
                           max_attempts: int = MAX_RECONNECT_ATTEMPTS) -> dict[str, object]:
    schedule = backoff_schedule(max_attempts)
    for attempt, delay in enumerate(schedule, 1):
        mesh.round += int(delay)
        if mesh.connect(node_id, target):
            return {"reconnected": True, "attempts": attempt, "schedule": schedule[:attempt]}
    return {"reconnected": False, "attempts": max_attempts, "schedule": schedule}


def relay_reservation(mesh: FaultMesh) -> dict[str, object]:
    attempts = 0
    for relay, up in mesh.relays.items():
        attempts += 1
        if attempts > MAX_RECONNECT_ATTEMPTS:
            break
        if up:
            return {"relay": relay, "attempts": attempts}
    return {"relay": None, "attempts": attempts}


def _fully_connect(mesh: FaultMesh) -> None:
    ids = sorted(mesh.nodes)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            mesh.connect(a, b)


def scenario_abrupt_disconnect() -> dict[str, object]:
    mesh = FaultMesh(["a", "b", "c", "d"])
    _fully_connect(mesh)
    mesh.broadcast("a", "m1")
    mesh.nodes["a"].down = True  # abrupt: no graceful goodbye
    mesh.keepalive_tick()
    delivered = mesh.broadcast("b", "m2")
    ok = (
        delivered == 2
        and "m2" in mesh.nodes["c"].dedup
        and "m2" in mesh.nodes["d"].dedup
        and "m2" not in mesh.nodes["a"].dedup
        and mesh.resource_bounds_ok()
    )
    return {"scenario": "abrupt_disconnect", "passed": ok,
            "details": {"delivered_after_fault": delivered, "active_nodes": 3}}


def scenario_bootstrap_outage_recovery() -> dict[str, object]:
    mesh = FaultMesh(["a", "b", "c"])
    mesh.connect("a", "b")
    mesh.bootstrap_up = False
    joined_via_cache = False
    for peer in sorted(mesh.peer_cache["c"]):
        if mesh.connect("c", peer):
            joined_via_cache = True
            break
    mesh.bootstrap_up = True
    before = len(mesh.nodes["c"].connected)
    mesh.connect("c", "a")  # re-attempt after restore must not duplicate
    after = len(mesh.nodes["c"].connected)
    ok = joined_via_cache and before == after and mesh.resource_bounds_ok()
    return {"scenario": "bootstrap_outage_recovery", "passed": ok,
            "details": {"joined_via_peer_cache": joined_via_cache,
                        "duplicate_connections_after_restore": after - before}}


def scenario_relay_failover() -> dict[str, object]:
    mesh = FaultMesh(["a"])
    mesh.relays["relay-1"] = False
    result = relay_reservation(mesh)
    ok = (
        result["relay"] == "relay-2"
        and result["attempts"] == 2
        and result["attempts"] <= MAX_RECONNECT_ATTEMPTS
    )
    return {"scenario": "relay_failover", "passed": ok, "details": result}


def scenario_reconnect_backoff() -> dict[str, object]:
    mesh = FaultMesh(["a", "b"])
    mesh.connect("a", "b")
    mesh.nodes["b"].down = True
    result = reconnect_with_backoff(mesh, "a", "b")
    expected_schedule = [1.0, 2.0, 4.0, 8.0, 8.0]
    mesh.nodes["b"].down = False
    recovery = reconnect_with_backoff(mesh, "a", "b")
    ok = (
        result["reconnected"] is False
        and result["attempts"] == MAX_RECONNECT_ATTEMPTS
        and result["schedule"] == expected_schedule
        and recovery["reconnected"] is True
        and recovery["attempts"] == 1
        and mesh.resource_bounds_ok()
    )
    return {"scenario": "reconnect_backoff", "passed": ok,
            "details": {"failed_schedule": result["schedule"],
                        "recovery_attempts": recovery["attempts"],
                        "max_attempts": MAX_RECONNECT_ATTEMPTS,
                        "backoff_cap_seconds": MAX_BACKOFF_SECONDS}}


def scenario_partial_partition() -> dict[str, object]:
    mesh = FaultMesh(["a", "b", "c", "d"])
    _fully_connect(mesh)
    cut = [("a", "c"), ("a", "d"), ("b", "c"), ("b", "d")]
    removed: list[tuple[str, str]] = []
    for x, y in cut:
        if y in mesh.nodes[x].connected:
            mesh.nodes[x].connected.discard(y)
            mesh.nodes[y].connected.discard(x)
            removed.append((x, y))
    mesh.broadcast("a", "m-part")
    leaked_during_partition = any(
        "m-part" in mesh.nodes[p].dedup for p in ("c", "d")
    )
    for x, y in removed:
        mesh.connect(x, y)
    synced = 0
    for x, y in removed:
        for mid in list(mesh.nodes[x].log):
            if mid not in mesh.nodes[y].dedup and mesh.deliver(x, y, mid):
                synced += 1
        for mid in list(mesh.nodes[y].log):
            if mid not in mesh.nodes[x].dedup and mesh.deliver(y, x, mid):
                synced += 1
    healed = all("m-part" in mesh.nodes[p].dedup for p in ("a", "b", "c", "d"))
    ok = (
        not leaked_during_partition
        and healed
        and synced >= 2
        and mesh.resource_bounds_ok()
    )
    return {"scenario": "partial_partition", "passed": ok,
            "details": {"leaked_during_partition": leaked_during_partition,
                        "healed_after_rejoin": healed, "synced_messages": synced}}


def scenario_duplicate_delivery() -> dict[str, object]:
    mesh = FaultMesh(["a", "b"])
    mesh.connect("a", "b")
    first = mesh.deliver("a", "b", "m-dup")
    second = mesh.deliver("a", "b", "m-dup")
    node_b = mesh.nodes["b"]
    copies = list(node_b.inbox).count("m-dup")
    ok = (
        first and second
        and node_b.duplicates_suppressed == 1
        and copies == 1
        and mesh.resource_bounds_ok()
    )
    return {"scenario": "duplicate_delivery", "passed": ok,
            "details": {"stored_copies": copies,
                        "duplicates_suppressed": node_b.duplicates_suppressed}}


def scenario_stale_peer_eviction() -> dict[str, object]:
    mesh = FaultMesh(["a", "b", "c"])
    mesh.connect("a", "b")
    mesh.connect("a", "c")
    mesh.keepalive_tick()
    mesh.nodes["b"].down = True
    for _ in range(STALE_AFTER_ROUNDS + 1):
        mesh.keepalive_tick()
    evicted = mesh.evict_stale()
    delivered = mesh.broadcast("a", "m-stale")
    ok = (
        "b" in evicted
        and "b" not in mesh.peer_cache["a"]
        and "b" not in mesh.nodes["a"].connected
        and delivered == 1
        and mesh.resource_bounds_ok()
    )
    return {"scenario": "stale_peer_eviction", "passed": ok,
            "details": {"evicted": evicted, "delivered_after_eviction": delivered,
                        "stale_after_rounds": STALE_AFTER_ROUNDS}}


def scenario_revoked_peer_fail_closed() -> dict[str, object]:
    mesh = FaultMesh(["a", "b", "c"])
    mesh.connect("a", "b")
    mesh.connect("a", "c")
    mesh.revocations.add("a")
    reconnect_accepted = mesh.connect("a", "b")
    delivered = mesh.broadcast("a", "m-revoked")
    ok = (
        reconnect_accepted is False
        and delivered == 0
        and "m-revoked" not in mesh.nodes["b"].dedup
        and mesh.nodes["a"].rejected_connections == 1
        and mesh.resource_bounds_ok()
    )
    return {"scenario": "revoked_peer_fail_closed", "passed": ok,
            "details": {"reconnect_accepted": reconnect_accepted,
                        "deliveries_from_revoked": delivered,
                        "rejected_connections": mesh.nodes["a"].rejected_connections}}


SCENARIOS = (
    scenario_abrupt_disconnect,
    scenario_bootstrap_outage_recovery,
    scenario_relay_failover,
    scenario_reconnect_backoff,
    scenario_partial_partition,
    scenario_duplicate_delivery,
    scenario_stale_peer_eviction,
    scenario_revoked_peer_fail_closed,
)


def run_matrix() -> list[dict[str, object]]:
    return [scenario() for scenario in SCENARIOS]


def main() -> int:
    results = run_matrix()
    report = {
        "result_class": "simulated_process_local",
        "multi_host_evidence": False,
        "bounds": {
            "max_inbox": MAX_INBOX,
            "max_dedup": MAX_DEDUP,
            "max_backoff_seconds": MAX_BACKOFF_SECONDS,
            "max_reconnect_attempts": MAX_RECONNECT_ATTEMPTS,
            "stale_after_rounds": STALE_AFTER_ROUNDS,
        },
        "scenarios": results,
        "all_passed": all(item["passed"] for item in results),
    }
    print(json.dumps(report, indent=2))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
