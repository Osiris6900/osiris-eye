"""Contract and safety regression tests for the Python ingestion reference."""
import asyncio
import math
import struct
import time

import pytest

from osiris_api.ultra_fast_engine import (
    FastTick,
    LockFreeRingBuffer,
    UltraFastEngine,
    ZeroCopyDeserializer,
)


def packet(sequence=1, *, timestamp_ns=None, price=123.5, size=2.0):
    return struct.pack(
        "<QddI",
        time.time_ns() if timestamp_ns is None else timestamp_ns,
        price, size, sequence,
    )


def test_buffer_wrap_and_overflow():
    ring = LockFreeRingBuffer(2)
    for n in range(3):
        ring.push(FastTick("BTC", float(n), 1.0, n + 1, n))
    assert [x.sequence for x in ring.get_batch(10)] == [1, 2]
    assert ring.count == 0
    assert ring.dropped == 1


def test_invalid_capacity_and_batch():
    for capacity in (0, 3, -2):
        with pytest.raises(ValueError):
            LockFreeRingBuffer(capacity)
    with pytest.raises(ValueError):
        LockFreeRingBuffer(2).get_batch(-1)


def test_wire_contract_and_invalid_payloads():
    tick = ZeroCopyDeserializer.from_binary(packet())
    assert tick.price == 123.5
    assert tick.size == 2.0
    for payload in (b"", packet() + b"x", packet(price=math.nan),
                    packet(price=0), packet(size=-1), packet(timestamp_ns=0)):
        with pytest.raises(ValueError):
            ZeroCopyDeserializer.from_binary(payload)


def test_ingestion_rejects_stale_future_and_replay():
    async def run():
        engine = UltraFastEngine(capacity=2, max_stale_ms=1000)
        with pytest.raises(RuntimeError):
            await engine.ingest_raw_packet(packet(), "BTC")
        await engine.start()
        assert await engine.ingest_raw_packet(packet(1), "BTC")
        assert not await engine.ingest_raw_packet(packet(1), "BTC")
        assert not await engine.ingest_raw_packet(packet(0), "BTC")
        assert not await engine.ingest_raw_packet(packet(2, timestamp_ns=1), "BTC")
        assert not await engine.ingest_raw_packet(
            packet(2, timestamp_ns=time.time_ns() + 10_000_000_000), "BTC"
        )
        assert not await engine.ingest_raw_packet(packet(2), "")
        assert await engine.ingest_raw_packet(packet(2), "BTC")
        assert await engine.ingest_raw_packet(packet(3), "BTC")
        assert [t.sequence for t in engine.buffer.get_batch()] == [2, 3]
        assert engine.get_stats()["overwritten"] == 1
        assert engine.get_stats()["rejected"] == 4
    asyncio.run(run())
