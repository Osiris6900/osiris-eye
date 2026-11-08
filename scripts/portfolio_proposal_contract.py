"""Research-only portfolio proposal; not an execution instruction."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class PortfolioProposal:
    model_version: str
    instrument: str
    target_exposure: Decimal
    risk_budget: Decimal
    generated_at: datetime
    expires_at: datetime

    def validate(self) -> None:
        if not self.model_version or not self.instrument:
            raise ValueError("model and instrument required")
        if self.generated_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("timezone-aware timestamps required")
        if self.expires_at <= self.generated_at:
            raise ValueError("proposal expiry must follow generation")
        if not self.target_exposure.is_finite() or not self.risk_budget.is_finite():
            raise ValueError("non-finite exposure")
        if self.risk_budget <= 0 or abs(self.target_exposure) > self.risk_budget:
            raise ValueError("proposal exceeds risk budget")
