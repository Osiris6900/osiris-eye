"""Fail-closed pre-trade policy for a paper environment."""
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PaperRiskPolicy:
    max_order_notional: Decimal
    max_absolute_position: Decimal

    def check(self, *, side: str, quantity: Decimal, price: Decimal,
              current_position: Decimal, halted: bool, stale: bool) -> None:
        if halted or stale:
            raise ValueError("risk halt or stale feed")
        if side not in ("buy", "sell"):
            raise ValueError("invalid side")
        values = (quantity, price, current_position, self.max_order_notional,
                  self.max_absolute_position)
        if not all(x.is_finite() for x in values):
            raise ValueError("non-finite risk input")
        if quantity <= 0 or price <= 0 or self.max_order_notional <= 0 or self.max_absolute_position <= 0:
            raise ValueError("invalid risk limit")
        if quantity * price > self.max_order_notional:
            raise ValueError("order notional exceeds limit")
        delta = quantity if side == "buy" else -quantity
        if abs(current_position + delta) > self.max_absolute_position:
            raise ValueError("projected position exceeds limit")
