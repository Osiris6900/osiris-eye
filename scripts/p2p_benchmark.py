#!/usr/bin/env python3
"""Process-local simulated P2P gossip benchmark harness (issue #86).

Runs deterministic gossip fan-out simulations for 10/100/1000 peers and
reports delivery ratio, hop-count latency proxy, transmissions, duplicates
suppressed, wall/CPU time and peak memory. Results are SIMULATED and
process-local: they are not multi-host or production-scale evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import random
import sys
import time
import tracemalloc
from datetime import datetime, timezone

MAX_PEERS = 2000
MAX_MESSAGES = 500
MAX_FANOUT = 32
MAX_ROUNDS = 64


def _pick_targets(rng: random.Random, active: list[int], source: int, fanout: int) -> list[int]:
    """Pick up to ``fanout`` distinct active peers (not ``source``), deterministically."""
    k = min(fanout, len(active) - 1)
    if k <= 0:
        return []
    picked: set[int] = set()
    attempts = 0
    while len(picked) < k and attempts < k * 10 + 10:
        attempts += 1
        idx = rng.randrange(len(active))
        if active[idx] != source:
            picked.add(idx)
    # Fallback sweep guarantees exactly k targets near-full fan-out stays deterministic.
    if len(picked) < k:
        for idx in range(len(active)):
            if len(picked) >= k:
                break
            if active[idx] != source:
                picked.add(idx)
    return [active[idx] for idx in sorted(picked)]


def simulate_gossip(
    *,
    peers: int,
    fanout: int,
    messages: int,
    churn_rate: float = 0.0,
    seed: int = 1,
) -> dict[str, object]:
    """Simulate gossip fan-out over one mesh; returns raw counters (no timings)."""
    if not 2 <= peers <= MAX_PEERS:
        raise ValueError("peers_out_of_bounds")
    if not 1 <= fanout <= MAX_FANOUT:
        raise ValueError("fanout_out_of_bounds")
    if not 1 <= messages <= MAX_MESSAGES:
        raise ValueError("messages_out_of_bounds")
    if not 0.0 <= churn_rate < 1.0:
        raise ValueError("churn_rate_out_of_bounds")

    rng = random.Random(seed)
    ids = list(range(peers))
    down_until: dict[int, int] = {}

    total_transmissions = 0
    duplicates_suppressed = 0
    hops_all: list[int] = []
    max_send_queue = 0
    final_delivered = 0
    final_active = 0

    for message_index in range(messages):
        if churn_rate > 0.0:
            for peer in ids:
                if peer == ids[0] or down_until.get(peer, -1) > message_index:
                    continue
                if rng.random() < churn_rate:
                    down_until[peer] = message_index + 2  # down for ~2 message rounds
        active = [p for p in ids if down_until.get(p, -1) <= message_index]
        source = ids[0]

        received: dict[int, int] = {source: 0}
        frontier = [source]
        rounds = 0
        while frontier and rounds < MAX_ROUNDS:
            rounds += 1
            next_frontier: list[int] = []
            for peer in frontier:
                targets = _pick_targets(rng, active, peer, fanout)
                max_send_queue = max(max_send_queue, len(targets))
                for target in targets:
                    total_transmissions += 1
                    if target in received:
                        duplicates_suppressed += 1
                    else:
                        received[target] = rounds
                        next_frontier.append(target)
            frontier = next_frontier

        hops_all.extend(received[p] for p in active if p in received)
        final_delivered = sum(1 for p in active if p in received)
        final_active = len(active)

    mean_hops = (sum(hops_all) / len(hops_all)) if hops_all else 0.0
    return {
        "peers": peers,
        "fanout": fanout,
        "messages": messages,
        "churn_rate": churn_rate,
        "seed": seed,
        "total_transmissions": total_transmissions,
        "duplicates_suppressed": duplicates_suppressed,
        "mean_hops": round(mean_hops, 4),
        "max_hops": max(hops_all) if hops_all else 0,
        "max_per_node_send_queue": max_send_queue,
        "send_queue_bounded": max_send_queue <= fanout,
        "final_delivery_ratio": round(final_delivered / final_active, 6) if final_active else 0.0,
        "rounds_capped": MAX_ROUNDS,
    }


def run_benchmark(
    *,
    peers: int,
    fanout: int,
    messages: int,
    churn_rate: float = 0.0,
    seed: int = 1,
) -> dict[str, object]:
    """Run one simulated configuration and attach resource measurements."""
    tracemalloc.start()
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    result = simulate_gossip(
        peers=peers, fanout=fanout, messages=messages, churn_rate=churn_rate, seed=seed
    )
    cpu_seconds = time.process_time() - cpu_start
    wall_seconds = time.perf_counter() - wall_start
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    transmissions = int(result["total_transmissions"])
    return {
        **result,
        "wall_seconds": round(wall_seconds, 4),
        "cpu_seconds": round(cpu_seconds, 4),
        "peak_memory_bytes": peak_bytes,
        "transmissions_per_second": round(transmissions / wall_seconds, 1) if wall_seconds > 0 else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--peers", default="10,100,1000",
                        help="comma-separated peer counts (default: 10,100,1000)")
    parser.add_argument("--fanout", type=int, default=8, help="gossip fan-out (default: 8)")
    parser.add_argument("--messages", type=int, default=50,
                        help="messages broadcast per configuration (default: 50)")
    parser.add_argument("--churn", type=float, default=0.0,
                        help="per-message churn probability per peer (default: 0.0)")
    parser.add_argument("--seed", type=int, default=1, help="deterministic RNG seed")
    parser.add_argument("--output", default="-", help="JSON output path or '-' for stdout")
    args = parser.parse_args(argv)

    try:
        peer_counts = [int(part.strip()) for part in args.peers.split(",") if part.strip()]
        if not peer_counts:
            raise ValueError("empty peer list")
        runs = [
            run_benchmark(peers=count, fanout=args.fanout, messages=args.messages,
                          churn_rate=args.churn, seed=args.seed)
            for count in peer_counts
        ]
    except ValueError as exc:
        print(f"invalid benchmark configuration: {exc}", file=sys.stderr)
        return 2

    report = {
        "result_class": "simulated_process_local",
        "multi_host_evidence": False,
        "production_readiness_evidence": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
        },
        "runs": runs,
    }
    encoded = json.dumps(report, indent=2)
    if args.output == "-":
        print(encoded)
    else:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
        print(f"benchmark report written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
