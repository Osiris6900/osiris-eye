"""Validated configuration for paper-only market tick ingestion.

Advanced memory mapping, native lock-free buffers and multipath are not implemented.
Keep their switches disabled until implementation and acceptance evidence exist.
These presets do not enable a live feed or order routing.
"""
import math
from dataclasses import dataclass


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


@dataclass(frozen=True)
class UltraFastConfig:
    RING_BUFFER_SIZE: int = 65_536
    NUM_WORKERS: int = 1
    BATCH_SIZE: int = 1_000
    ENABLE_PREFETCH: bool = False
    PREFETCH_WINDOW: int = 1_000
    ENABLE_MULTIPATH: bool = False
    MAX_PATHS_PER_SYMBOL: int = 1
    STATS_INTERVAL_S: float = 5.0
    LATENCY_TRACKING_SIZE: int = 10_000
    FAIL_CLOSED_ON_STALE: bool = True
    MAX_STALE_MS: int = 100
    USE_MEMORY_MAPPING: bool = False
    PREALLOCATE_BUFFERS: bool = False

    def __post_init__(self) -> None:
        n = self.RING_BUFFER_SIZE
        if not _positive_int(n) or n & (n - 1):
            raise ValueError("RING_BUFFER_SIZE must be a positive power of two")
        if not all(_positive_int(value) for value in (
            self.NUM_WORKERS, self.BATCH_SIZE, self.MAX_STALE_MS,
            self.PREFETCH_WINDOW, self.MAX_PATHS_PER_SYMBOL, self.LATENCY_TRACKING_SIZE,
        )):
            raise ValueError("workers, batch size and staleness limit must be positive")
        interval = self.STATS_INTERVAL_S
        if (
            isinstance(interval, bool)
            or not isinstance(interval, (int, float))
            or not math.isfinite(interval)
            or interval <= 0
        ):
            raise ValueError("stats interval must be a positive finite number")
        if self.BATCH_SIZE > n:
            raise ValueError("BATCH_SIZE cannot exceed RING_BUFFER_SIZE")
        if self.FAIL_CLOSED_ON_STALE is not True:
            raise ValueError("disabling stale-data rejection is not supported")
        if (
            self.ENABLE_PREFETCH is not False
            or self.ENABLE_MULTIPATH is not False
            or self.USE_MEMORY_MAPPING is not False
            or self.PREALLOCATE_BUFFERS is not False
        ):
            raise ValueError("requested accelerated feature is not implemented")


DEVELOPMENT_CONFIG = UltraFastConfig(RING_BUFFER_SIZE=131_072)
PRODUCTION_CONFIG = UltraFastConfig(RING_BUFFER_SIZE=4_194_304)
