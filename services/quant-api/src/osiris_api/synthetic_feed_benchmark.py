"""Local synthetic benchmark for the paper tick adapter.

This measures in-process Python ingestion only. It is not a network, kernel,
or exchange latency measurement, and it does not route orders.
"""
from __future__ import annotations

import struct
import time

from .adapters.ultra_fast_feed import UltraFastMarketFeed


def simulate_network_traffic(
    ticks: int = 1_000_000,
    *,
    buffer_size: int = 65_536,
    batch_size: int = 1_000,
) -> dict:
    if isinstance(ticks, bool) or not isinstance(ticks, int) or ticks < 0:
        raise ValueError("ticks must be a nonnegative integer")
    if (
        isinstance(batch_size, bool)
        or not isinstance(batch_size, int)
        or batch_size <= 0
        or batch_size > buffer_size
    ):
        raise ValueError("ticks must be nonnegative; batch_size in 1..buffer_size")
    feed = UltraFastMarketFeed(buffer_size=buffer_size, max_age_ns=30_000_000_000)
    symbols = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
    start = time.perf_counter_ns()
    for i in range(ticks):
        symbol = symbols[i % len(symbols)]
        packet = struct.pack("<QddI", time.time_ns(), 50_000.0 + i % 100, 1.5, i)
        if not feed.ingest_binary(packet, symbol):
            raise RuntimeError(f"synthetic ingestion rejected at tick {i}: {feed.stats()}")
        if (i + 1) % batch_size == 0:
            feed.get_batch(batch_size)
    feed.get_batch(buffer_size)
    elapsed_ns = max(1, time.perf_counter_ns() - start)
    return {
        "mode": "synthetic_local_only",
        "total_ticks": feed.accepted,
        "rejected": feed.rejected,
        "elapsed_seconds": elapsed_ns / 1_000_000_000,
        "ticks_per_second": feed.accepted * 1_000_000_000 / elapsed_ns,
        "buffer_capacity": buffer_size,
        "live_trading_authorized": False,
    }
