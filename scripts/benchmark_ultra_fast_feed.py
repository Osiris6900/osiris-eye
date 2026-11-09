"""Opt-in local benchmark for Python binary tick ingestion.

Usage: PYTHONPATH=services/quant-api/src python scripts/benchmark_ultra_fast_feed.py
Numbers are host-specific; this is not an exchange/network latency benchmark.
"""
import argparse
import asyncio
import json
import struct
import time

from osiris_api.ultra_fast_engine import UltraFastEngine


async def main(count: int = 100_000) -> None:
    if not 1 <= count <= 131_072:
        raise ValueError("count must be between 1 and 131072 to avoid buffer overflow")
    engine = UltraFastEngine(capacity=131_072, max_stale_ms=60_000)
    await engine.start()
    started = time.perf_counter_ns()
    for sequence in range(1, count + 1):
        payload = struct.pack("<QddI", time.time_ns(), 100.0, 1.0, sequence)
        if not await engine.ingest_raw_packet(payload, "BENCH"):
            raise RuntimeError(f"tick {sequence} rejected")
    elapsed_ns = time.perf_counter_ns() - started
    await engine.stop()
    drained = engine.drain(count)
    if len(drained) != count or any(tick.sequence != i for i, tick in enumerate(drained, 1)):
        raise RuntimeError("benchmark FIFO drain lost or reordered ticks")
    if engine.rejected or engine.buffer.dropped or engine.buffer.count != 0:
        raise RuntimeError("benchmark lost or rejected ticks")
    print(json.dumps({
        "count": count,
        "elapsed_seconds": elapsed_ns / 1e9,
        "throughput_ticks_per_second": round(count * 1e9 / elapsed_ns, 2),
        "average_loop_ns_per_tick": round(elapsed_ns / count, 2),
        "overwritten": engine.buffer.dropped,
        "rejected": engine.rejected,
        "scope": "local Python encode+decode+validate+buffer, no network",
    }, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100_000)
    args = parser.parse_args()
    asyncio.run(main(args.count))
