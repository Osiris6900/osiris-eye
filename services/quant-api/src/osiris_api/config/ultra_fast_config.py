"""Validated configuration for paper-only market tick ingestion.

Advanced memory mapping, native lock-free buffers and multipath are not implemented.
Keep their switches disabled until implementation and acceptance evidence exist.
"""
from dataclasses import dataclass


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
        if n <= 0 or n & (n - 1):
            raise ValueError("RING_BUFFER_SIZE must be a positive power of two")
        if self.NUM_WORKERS <= 0 or self.BATCH_SIZE <= 0 or self.MAX_STALE_MS <= 0:
            raise ValueError("workers, batch size and staleness limit must be positive")
        if self.BATCH_SIZE > n:
            raise ValueError("BATCH_SIZE cannot exceed RING_BUFFER_SIZE")
        if not self.FAIL_CLOSED_ON_STALE:
            raise ValueError("disabling stale-data rejection is not supported")
        if (self.ENABLE_PREFETCH or self.ENABLE_MULTIPATH or self.USE_MEMORY_MAPPING
                or self.PREALLOCATE_BUFFERS):
            raise ValueError("requested accelerated feature is not implemented")


DEVELOPMENT_CONFIG = UltraFastConfig(RING_BUFFER_SIZE=131_072)
PRODUCTION_CONFIG = UltraFastConfig(RING_BUFFER_SIZE=4_194_304)
