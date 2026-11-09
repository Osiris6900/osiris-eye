"""Opt-in local benchmark for Python binary tick ingestion.

Usage: PYTHONPATH=services/quant-api/src python scripts/benchmark_ultra_fast_feed.py
Numbers are host-specific; this is not an exchange/network latency benchmark.
"""
import asyncio
import json
import struct
import time

from osiris_api.ultra_fast_engine import UltraFastEngine


async def main(count: int = 100_000) -> None:
    engine = UltraFastEngine(capacity=131_072, max_stale_ms=60_000)
    await engine.start()
    started = time.perf_counter_ns()
    for sequence in range(1, count + 1):
        payload = struct.pack("<QddI", time.time_ns(), 100.0, 1.0, sequence)
        if not await engine.ingest_raw_packet(payload, "BENCH"):
            raise RuntimeError(f"tick {sequence} rejected")
    elapsed_ns = time.perf_counter_ns() - started
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
    asyncio.run(main())
