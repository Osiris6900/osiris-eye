"""Conservative pre-trade gate. Not wired to live order execution."""
from dataclasses import dataclass
from math import isfinite
from typing import Any
from .input_validator import OrderInputValidator


@dataclass(frozen=True)
class RiskResult:
    allowed: bool
    reason: str
    checks: list[str]


class RiskEngine:
    """Require explicit portfolio risk authorization; otherwise deny orders."""

    def __init__(self, config: dict[str, Any]):
        threshold = config.get("stale_data_threshold_seconds")
        if (isinstance(threshold, bool) or not isinstance(threshold, (int, float))
                or not isfinite(threshold) or threshold <= 0):
            raise ValueError("stale_data_threshold_seconds must be finite and positive")
        self.stale_threshold = threshold
        self.kill_switch_active = True  # fail closed until explicitly configured
        self._portfolio_gate = None

    def check_order(self, order_dict: dict, market_data_age: float) -> RiskResult:
        errors = OrderInputValidator.validate(order_dict, market_data_age)
        if errors:
            return RiskResult(False, "Invalid inputs: " + ", ".join(e.field for e in errors),
                              ["input_validation"])
        if self.kill_switch_active:
            return RiskResult(False, "Kill switch active", ["kill_switch"])
        if market_data_age > self.stale_threshold:
            return RiskResult(False, "Stale market data", ["data_freshness"])
        # Position, daily loss, drawdown, account state and execution authorization
        # must be checked by a real integrated portfolio gate before permitting trades.
        return RiskResult(False, "Portfolio risk checks not integrated", ["portfolio_gate"])
