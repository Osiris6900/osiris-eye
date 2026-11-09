"""Contract and safety regression tests for the Python ingestion reference."""
import asyncio
import math
import struct
import time

import pytest

from osiris_api.ultra_fast_config import PRODUCTION_CONFIG, UltraFastConfig
from osiris_api.ultra_fast_engine import (
    FastTick,
    LockFreeRingBuffer,
    UltraFastEngine,
    ZeroCopyDeserializer,
    create_engine,
)


def packet(sequence=1, *, timestamp_ns=None, price=123.5, size=2.0):
    return struct.pack(
        "<QddI",
        time.time_ns() if timestamp_ns is None else timestamp_ns,
        price, size, sequence,
    )


def test_flat_config_import():
    assert isinstance(PRODUCTION_CONFIG, UltraFastConfig)
    assert PRODUCTION_CONFIG.FAIL_CLOSED_ON_STALE


def test_engine_factory_uses_config_and_rejects_unsafe_stale_setting():
    config = UltraFastConfig(RING_BUFFER_SIZE=8, MAX_STALE_MS=250)
    engine = create_engine(config)
    assert engine.buffer.capacity == 8
    assert engine.max_stale_ns == 250_000_000
    with pytest.raises(ValueError, match="stale-feed"):
        create_engine(UltraFastConfig(FAIL_CLOSED_ON_STALE=False))


def test_buffer_wrap_and_overflow():
    ring = LockFreeRingBuffer(2)
    for n in range(3):
        ring.push(FastTick("BTC", float(n), 1.0, n + 1, n))
    assert [x.sequence for x in ring.get_batch(10)] == [1, 2]
    assert ring.count == 0
    assert ring.dropped == 1


def test_invalid_capacity_and_batch():
    for capacity in (0, 3, -2, 2.0, True, "2"):
        with pytest.raises(ValueError):
            LockFreeRingBuffer(capacity)
    for size in (-1, 1.5, True, "2"):
        with pytest.raises(ValueError):
            LockFreeRingBuffer(2).get_batch(size)


def test_wire_contract_and_invalid_payloads():
    tick = ZeroCopyDeserializer.from_binary(packet())
    assert tick.price == 123.5
    assert tick.size == 2.0
    for payload in (b"", packet() + b"x", packet(price=math.nan),
                    packet(price=0), packet(size=-1), packet(timestamp_ns=0)):
        with pytest.raises(ValueError):
            ZeroCopyDeserializer.from_binary(payload)


def test_symbol_normalization_prevents_duplicate_sequence_alias():
    async def run():
        engine = UltraFastEngine(capacity=2, max_stale_ms=1000)
        await engine.start()
        assert await engine.ingest_raw_packet(packet(1), " BTC ")
        assert not await engine.ingest_raw_packet(packet(1), "BTC")
        assert [tick.symbol for tick in engine.buffer.get_batch()] == ["BTC"]
    asyncio.run(run())


def test_stale_threshold_requires_integer():
    for value in (1.5, "100", True):
        with pytest.raises(ValueError, match="integer"):
            UltraFastEngine(max_stale_ms=value)


def test_stop_blocks_new_admission_but_preserves_queued_ticks():
    async def run():
        engine = UltraFastEngine(capacity=2, max_stale_ms=1000)
        await engine.start()
        assert await engine.ingest_raw_packet(packet(1), "BTC")
        await engine.stop()
        assert not engine.is_running
        with pytest.raises(RuntimeError, match="not started"):
            await engine.ingest_raw_packet(packet(2), "BTC")
        assert [tick.sequence for tick in engine.drain()] == [1]
        assert engine.drain() == []
    asyncio.run(run())


def test_drain_batch_is_bounded_and_fifo():
    async def run():
        engine = UltraFastEngine(capacity=4, max_stale_ms=1000)
        await engine.start()
        for seq in (1, 2, 3):
            assert await engine.ingest_raw_packet(packet(seq), "BTC")
        assert [tick.sequence for tick in engine.drain(2)] == [1, 2]
        assert engine.buffer.count == 1
        assert [tick.sequence for tick in engine.drain(2)] == [3]
        assert engine.drain(0) == []
        with pytest.raises(ValueError):
            engine.drain(-1)
    asyncio.run(run())


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
        assert not await engine.ingest_raw_packet(packet(2), None)
        assert await engine.ingest_raw_packet(packet(2), "BTC")
        assert await engine.ingest_raw_packet(packet(3), "BTC")
        assert [t.sequence for t in engine.buffer.get_batch()] == [2, 3]
        assert engine.get_stats()["overwritten"] == 1
        assert engine.get_stats()["rejected"] == 6
    asyncio.run(run())
