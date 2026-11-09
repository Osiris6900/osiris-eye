"""Deterministic, paper-only order acceptance guard (no broker connectivity)."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum


class Decision(str, Enum):
    ACCEPTED = "accepted"
    REPLAY = "replay"
    REJECTED = "rejected"


@dataclass(frozen=True)
class PaperOrder:
    client_order_id: str
    instrument: str
    quantity: Decimal
    reference_price: Decimal


@dataclass(frozen=True)
class GuardResult:
    decision: Decision
    reason: str


class PaperOrderGuard:
    """Fail-closed paper order gate; replaying an ID cannot create another order.

    In-memory state is intentionally ephemeral. A durable journal is required
    before use in a multi-process or restart-safe paper execution service.
    """

    def __init__(self, max_notional: Decimal) -> None:
        if not max_notional.is_finite() or max_notional <= 0:
            raise ValueError("max_notional must be finite and positive")
        self.max_notional = max_notional
        self._accepted: dict[str, PaperOrder] = {}
        self.halted = True

    def set_paper_enabled(self, enabled: bool) -> None:
        self.halted = not enabled

    def evaluate(self, order: PaperOrder, *, risk_approved: bool) -> GuardResult:
        if not order.client_order_id.strip() or not order.instrument.strip():
            return GuardResult(Decision.REJECTED, "missing_identity")
        previous = self._accepted.get(order.client_order_id)
        if previous is not None:
            if previous == order:
                return GuardResult(Decision.REPLAY, "idempotent_replay")
            return GuardResult(Decision.REJECTED, "idempotency_conflict")
        if self.halted:
            return GuardResult(Decision.REJECTED, "paper_halted")
        if not risk_approved:
            return GuardResult(Decision.REJECTED, "risk_not_approved")
        try:
            notional = order.quantity * order.reference_price
        except (InvalidOperation, TypeError):
            return GuardResult(Decision.REJECTED, "invalid_amount")
        if (
            not order.quantity.is_finite()
            or not order.reference_price.is_finite()
            or order.quantity <= 0
            or order.reference_price <= 0
            or not notional.is_finite()
            or notional > self.max_notional
        ):
            return GuardResult(Decision.REJECTED, "invalid_or_excess_notional")
        self._accepted[order.client_order_id] = order
        return GuardResult(Decision.ACCEPTED, "paper_only")
