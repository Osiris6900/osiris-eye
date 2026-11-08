"""Deterministic, sandbox-only paper order ledger. No exchange connectivity."""
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class OrderState(str, Enum):
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"


@dataclass
class PaperOrder:
    key: str
    symbol: str
    side: str
    quantity: Decimal
    filled: Decimal = Decimal("0")
    state: OrderState = OrderState.ACCEPTED


@dataclass
class PaperLedger:
    orders: dict[str, PaperOrder] = field(default_factory=dict)
    cash: Decimal = Decimal("0")
    positions: dict[str, Decimal] = field(default_factory=dict)
    fees: Decimal = Decimal("0")
    halted: bool = False

    def submit(self, key: str, symbol: str, side: str, quantity: Decimal) -> PaperOrder:
        if key in self.orders:
            order = self.orders[key]
            if (order.symbol, order.side, order.quantity) != (symbol, side, quantity):
                raise ValueError("idempotency key reused with different order")
            return order
        if self.halted:
            raise ValueError("paper trading halted")
        if not key or not symbol or side not in ("buy", "sell") or not quantity.is_finite() or quantity <= 0:
            raise ValueError("invalid order")
        order = PaperOrder(key, symbol, side, quantity)
        self.orders[key] = order
        return order

    def fill(self, key: str, quantity: Decimal, price: Decimal, fee: Decimal = Decimal("0")) -> PaperOrder:
        if self.halted:
            raise ValueError("paper trading halted")
        order = self.orders[key]
        if order.state in (OrderState.CANCELED, OrderState.FILLED):
            raise ValueError("terminal order")
        if (not quantity.is_finite() or quantity <= 0 or not price.is_finite() or price <= 0
                or not fee.is_finite() or fee < 0 or order.filled + quantity > order.quantity):
            raise ValueError("invalid paper fill")
        sign = Decimal("1") if order.side == "buy" else Decimal("-1")
        self.positions[order.symbol] = self.positions.get(order.symbol, Decimal("0")) + sign * quantity
        self.cash -= sign * quantity * price + fee
        self.fees += fee
        order.filled += quantity
        order.state = OrderState.FILLED if order.filled == order.quantity else OrderState.PARTIALLY_FILLED
        return order

    def cancel(self, key: str) -> PaperOrder:
        order = self.orders[key]
        if order.state == OrderState.FILLED:
            raise ValueError("filled order cannot be canceled")
        order.state = OrderState.CANCELED
        return order

    def halt(self) -> None:
        self.halted = True
