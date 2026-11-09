"""Validated, bounded paper-market tick ingestion (no exchange connection or live orders)."""
from __future__ import annotations

import math
import struct
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from osiris_api.config.ultra_fast_config import UltraFastConfig

_BINARY_TICK = struct.Struct("<QddI")


@dataclass(frozen=True, slots=True)
class TickData:
    symbol: str
    price: float
    size: float
    timestamp_ns: int
    exchange_ts: int
    sequence: int
    received_ns: int


class BoundedTickBuffer:
    """Single-event-loop FIFO; overflow is explicit, never silently overwritten."""

    def __init__(self, capacity: int = 65536):
        if (
            isinstance(capacity, bool)
            or not isinstance(capacity, int)
            or capacity <= 0
            or capacity & (capacity - 1)
        ):
            raise ValueError("capacity must be a positive power of two")
        self.capacity = capacity
        self._ticks: deque[TickData] = deque()
        self.rejected_overflow = 0

    def push(self, tick: TickData) -> bool:
        if len(self._ticks) >= self.capacity:
            self.rejected_overflow += 1
            return False
        self._ticks.append(tick)
        return True

    def pop_batch(self, limit: int = 1000) -> list[TickData]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        return [self._ticks.popleft() for _ in range(min(limit, len(self._ticks)))]

    @property
    def utilization(self) -> float:
        return len(self._ticks) / self.capacity


class UltraFastMarketFeed:
    """Paper-only feed adapter. No sub-microsecond or lock-free guarantees."""

    def __init__(self, buffer_size: int = 65536, max_age_ns: int = 30_000_000_000):
        self.buffer = BoundedTickBuffer(buffer_size)
        self.max_age_ns = max_age_ns
        self.last_sequence: dict[str, int] = {}
        self.subscribers: dict[str, list[Callable[[TickData], None]]] = {}
        self.accepted = 0
        self.rejected = 0

    def ingest(self, symbol: str, price: float, size: float, exchange_ts_ns: int,
               sequence: int, *, received_ns: int | None = None) -> bool:
        try:
            now = time.time_ns() if received_ns is None else received_ns
            if not isinstance(symbol, str):
                raise TypeError("symbol required")
            symbol = symbol.strip()
            if (
                not symbol
                or isinstance(price, bool) or not isinstance(price, (int, float))
                or not math.isfinite(price) or price <= 0
                or isinstance(size, bool) or not isinstance(size, (int, float))
                or not math.isfinite(size) or size <= 0
                or isinstance(exchange_ts_ns, bool) or not isinstance(exchange_ts_ns, int)
                or isinstance(sequence, bool) or not isinstance(sequence, int)
                or isinstance(now, bool) or not isinstance(now, int)
                or exchange_ts_ns <= 0 or exchange_ts_ns > now
                or now - exchange_ts_ns > self.max_age_ns or sequence < 0
                or (symbol in self.last_sequence and sequence <= self.last_sequence[symbol])
            ):
                raise ValueError("tick rejected")
        except (TypeError, ValueError, OverflowError):
            self.rejected += 1
            return False
        tick = TickData(symbol, price, size, now, exchange_ts_ns, sequence, now)
        if not self.buffer.push(tick):
            self.rejected += 1
            return False
        self.last_sequence[symbol] = sequence
        self.accepted += 1
        for callback in self.subscribers.get(symbol, ()):
            try:
                callback(tick)
            except Exception:
                # Callback failure must not retroactively invalidate accepted data.
                pass
        return True

    def ingest_binary(self, payload: bytes, symbol: str, *,
                      received_ns: int | None = None) -> bool:
        if len(payload) != _BINARY_TICK.size:
            self.rejected += 1
            return False
        exchange_ts_ns, price, size, sequence = _BINARY_TICK.unpack(payload)
        return self.ingest(symbol, price, size, exchange_ts_ns, sequence,
                           received_ns=received_ns)

    def get_batch(self, max_ticks: int = 1000) -> list[TickData]:
        return self.buffer.pop_batch(max_ticks)

    def subscribe(self, symbol: str, callback: Callable[[TickData], None]) -> None:
        self.subscribers.setdefault(symbol, []).append(callback)

    def unsubscribe(self, symbol: str, callback: Callable[[TickData], None]) -> None:
        callbacks = self.subscribers.get(symbol, [])
        if callback in callbacks:
            callbacks.remove(callback)
        if not callbacks:
            self.subscribers.pop(symbol, None)

    def stats(self) -> dict[str, float | int]:
        return {"accepted": self.accepted, "rejected": self.rejected,
                "overflow": self.buffer.rejected_overflow,
                "buffer_utilization": self.buffer.utilization}


class OSIRISUltraFastAdapter:
    """Explicit JSON adapter; Binance trade IDs are sequences, 'm' is NOT."""

    def __init__(self, api_instance=None, *, config: UltraFastConfig | None = None,
                 buffer_size: int | None = None):
        self.api = api_instance
        self.config = config or UltraFastConfig()
        self.feed = UltraFastMarketFeed(
            buffer_size=self.config.RING_BUFFER_SIZE if buffer_size is None else buffer_size,
            max_age_ns=self.config.MAX_STALE_MS * 1_000_000,
        )

    def process_websocket_message(self, message: dict, *,
                                  received_ns: int | None = None) -> bool:
        try:
            symbol = str(message.get("s") or message["symbol"])
            price = float(message.get("p", message.get("price")))
            size = float(message.get("q", message.get("quantity")))
            timestamp_ms = int(message.get("T", message.get("time")))
            sequence = int(message.get("t", message.get("id")))
        except (KeyError, ValueError, TypeError, OverflowError):
            self.feed.rejected += 1
            return False
        return self.feed.ingest(symbol, price, size, timestamp_ms * 1_000_000,
                                sequence, received_ns=received_ns)
