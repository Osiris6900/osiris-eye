"""Normalized market observations with strict timestamps and sequence validation."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal


@dataclass(frozen=True)
class MarketObservation:
    source: str
    venue: str
    symbol: str
    event_time: datetime
    received_time: datetime
    sequence: int
    price: Decimal

    def validate(self, max_lag_seconds: float = 5.0) -> None:
        if not self.source or not self.venue or not self.symbol:
            raise ValueError("missing source or instrument")
        if self.event_time.tzinfo is None or self.received_time.tzinfo is None:
            raise ValueError("timezone-aware timestamps required")
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("invalid sequence")
        if not self.price.is_finite() or self.price <= 0:
            raise ValueError("invalid price")
        lag = (self.received_time.astimezone(timezone.utc) -
               self.event_time.astimezone(timezone.utc)).total_seconds()
        if lag < 0 or lag > max_lag_seconds:
            raise ValueError("future or stale observation")
