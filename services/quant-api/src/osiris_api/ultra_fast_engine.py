"""Bounded binary market-tick ingestion (Python reference implementation).

Not lock-free or zero-copy; benchmark on target hardware before latency claims.
Wire format: little-endian uint64 timestamp_ns, float64 price, float64 size,
uint32 sequence (28 bytes). No live-order execution is performed here.
"""
import logging
import math
import struct
import time
from dataclasses import dataclass

from .ultra_fast_config import PRODUCTION_CONFIG, UltraFastConfig

logger = logging.getLogger(__name__)
_TICK = struct.Struct("<QddI")


@dataclass(slots=True)
class FastTick:
    symbol: str
    price: float
    size: float
    timestamp_ns: int
    sequence: int


class LockFreeRingBuffer:
    """Single-event-loop bounded FIFO; overwrites oldest entries on overflow.

    Kept under the supplied API name for compatibility. Not thread-safe or lock-free.
    Capacity must be a power of two.
    """

    def __init__(self, capacity: int = 2_097_152):
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0 or capacity & (capacity - 1):
            raise ValueError("capacity must be a positive power of two")
        self.capacity = capacity
        self.buffer: list[FastTick | None] = [None] * capacity
        self.head = 0
        self.tail = 0
        self.count = 0
        self.dropped = 0
        self._mask = capacity - 1

    def push(self, tick: FastTick) -> None:
        if self.count == self.capacity:
            self.buffer[self.head] = None
            self.head = (self.head + 1) & self._mask
            self.count -= 1
            self.dropped += 1
        self.buffer[self.tail] = tick
        self.tail = (self.tail + 1) & self._mask
        self.count += 1

    def get_batch(self, size: int = 1000) -> list[FastTick]:
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("size must be nonnegative")
        batch = []
        for _ in range(min(size, self.count)):
            tick = self.buffer[self.head]
            self.buffer[self.head] = None
            self.head = (self.head + 1) & self._mask
            self.count -= 1
            if tick is not None:
                batch.append(tick)
        return batch


class ZeroCopyDeserializer:
    """Binary decoder; unpacking creates Python objects, so it is not zero-copy."""

    @staticmethod
    def from_binary(raw_data: bytes) -> FastTick:
        if len(raw_data) != _TICK.size:
            raise ValueError(f"expected {_TICK.size} bytes, got {len(raw_data)}")
        ts, price, size, seq = _TICK.unpack(raw_data)
        if not math.isfinite(price) or price <= 0:
            raise ValueError("price must be finite and positive")
        if not math.isfinite(size) or size <= 0:
            raise ValueError("size must be finite and positive")
        if ts == 0:
            raise ValueError("timestamp_ns must be positive")
        return FastTick("AUTO", price, size, ts, seq)


class UltraFastEngine:
    """Single-event-loop reference engine; callers serialize producer access."""

    def __init__(self, capacity: int = 2_097_152, max_stale_ms: int = 100):
        if isinstance(max_stale_ms, bool) or not isinstance(max_stale_ms, int):
            raise ValueError("max_stale_ms must be an integer")
        if max_stale_ms < 0:
            raise ValueError("max_stale_ms must be nonnegative")
        self.buffer = LockFreeRingBuffer(capacity)
        self.max_stale_ns = max_stale_ms * 1_000_000
        self.is_running = False
        self.total_processed = 0
        self.rejected = 0
        self.start_time_ns = 0
        self.last_sequence: dict[str, int] = {}

    async def start(self) -> None:
        self.is_running = True
        self.start_time_ns = time.monotonic_ns()
        logger.info("Bounded market-tick ingestion started")

    async def stop(self) -> None:
        """Stop admission without silently discarding already buffered ticks."""
        self.is_running = False
        logger.info("Bounded market-tick ingestion stopped")

    async def ingest_raw_packet(self, raw_bytes: bytes, symbol: str) -> bool:
        if not self.is_running:
            raise RuntimeError("engine is not started")
        try:
            if not isinstance(symbol, str) or not symbol.strip():
                raise ValueError("symbol required")
            tick = ZeroCopyDeserializer.from_binary(raw_bytes)
            symbol = symbol.strip()
            tick.symbol = symbol
            now_ns = time.time_ns()
            if tick.timestamp_ns > now_ns or now_ns - tick.timestamp_ns > self.max_stale_ns:
                raise ValueError("future or stale tick")
            previous = self.last_sequence.get(symbol)
            if previous is not None and tick.sequence <= previous:
                raise ValueError("duplicate or out-of-order sequence")
            self.buffer.push(tick)
            self.last_sequence[symbol] = tick.sequence
            self.total_processed += 1
            return True
        except (ValueError, struct.error, TypeError) as exc:
            self.rejected += 1
            logger.warning("Rejected market tick: %s", exc)
            return False

    def drain(self, size: int = 1000) -> list[FastTick]:
        """Read admitted ticks in FIFO order; safe to call after stop."""
        return self.buffer.get_batch(size)

    def get_stats(self) -> dict:
        elapsed = ((time.monotonic_ns() - self.start_time_ns) / 1e9
                   if self.start_time_ns else 0)
        return {
            "ticks_per_second": self.total_processed / elapsed if elapsed > 0 else 0.0,
            "buffer_load": f"{self.buffer.count / self.buffer.capacity * 100:.2f}%",
            "total_ticks": self.total_processed,
            "rejected": self.rejected,
            "overwritten": self.buffer.dropped,
        }


def create_engine(config: UltraFastConfig = PRODUCTION_CONFIG) -> UltraFastEngine:
    """Construct a fail-closed engine from the explicit configuration."""
    if not config.FAIL_CLOSED_ON_STALE:
        raise ValueError("stale-feed rejection cannot be disabled")
    return UltraFastEngine(
        capacity=config.RING_BUFFER_SIZE,
        max_stale_ms=config.MAX_STALE_MS,
    )
