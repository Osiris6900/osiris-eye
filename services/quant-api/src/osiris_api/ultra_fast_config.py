"""Configuration defaults for bounded Python market-tick ingestion."""
from dataclasses import dataclass


@dataclass(frozen=True)
class UltraFastConfig:
    RING_BUFFER_SIZE: int = 2_097_152
    NUM_WORKERS: int = 16  # Reserved; Python reference engine runs on one event loop.
    BATCH_SIZE: int = 1000
    ENABLE_PREFETCH: bool = False  # Not implemented by the reference engine.
    FAIL_CLOSED_ON_STALE: bool = True
    MAX_STALE_MS: int = 100


PRODUCTION_CONFIG = UltraFastConfig()
