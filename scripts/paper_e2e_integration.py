"""Research proposal -> validated observation -> independent risk -> paper ledger.

Offline integration only. No exchange access, broker adapters or live orders.
"""
from datetime import datetime, timezone
from decimal import Decimal
from scripts.market_observation_contract import MarketObservation
from scripts.portfolio_proposal_contract import PortfolioProposal
from scripts.independent_paper_risk import PaperRiskPolicy
from scripts.paper_order_ledger import PaperLedger


def execute_paper_proposal(*, proposal: PortfolioProposal, observation: MarketObservation,
                           policy: PaperRiskPolicy, ledger: PaperLedger, key: str,
                           quantity: Decimal, side: str, now: datetime) -> None:
    """Submit and fill a deterministic synthetic order after all gates.

    This is a single-threaded demo only: production OMS requires atomic
    reservations, market microstructure simulation and persistent event logs.
    """
    proposal.validate()
    observation.validate()
    if now.tzinfo is None or now < proposal.generated_at or now > proposal.expires_at:
        raise ValueError("proposal expired or clock invalid")
    if observation.event_time > now:
        raise ValueError("future market observation")
    if proposal.instrument != observation.symbol:
        raise ValueError("instrument mismatch")
    if side not in ("buy", "sell") or quantity <= 0 or not quantity.is_finite():
        raise ValueError("invalid order quantity")
    target_delta = quantity if side == "buy" else -quantity
    current = ledger.positions.get(proposal.instrument, Decimal("0"))
    if current + target_delta != proposal.target_exposure:
        raise ValueError("order does not match approved portfolio target")
    policy.check(side=side, quantity=quantity, price=observation.price,
                 current_position=current, halted=ledger.halted, stale=False)
    ledger.submit(key, proposal.instrument, side, quantity)
    ledger.fill(key, quantity, observation.price)
