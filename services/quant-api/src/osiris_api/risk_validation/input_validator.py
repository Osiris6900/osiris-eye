"""Strict, fail-closed input validation for proposed orders."""
from dataclasses import dataclass
from math import isfinite
from typing import Any


@dataclass(frozen=True)
class ValidationError:
    field: str
    value: Any
    expected: str
    severity: str = "CRITICAL"


class OrderInputValidator:
    @staticmethod
    def validate(order_data: dict, market_data_age: float) -> list[ValidationError]:
        errors: list[ValidationError] = []
        if not isinstance(order_data, dict):
            return [ValidationError("order_data", order_data, "dict")]
        if (isinstance(market_data_age, bool)
                or not isinstance(market_data_age, (int, float))
                or not isfinite(market_data_age)
                or market_data_age < 0):
            errors.append(ValidationError("market_data_age", market_data_age,
                                          "finite non-negative number"))
        for name in ("quantity", "price"):
            value = order_data.get(name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not isfinite(value) or value <= 0):
                errors.append(ValidationError(name, value, "finite positive number"))
        for name in ("order_id", "symbol"):
            value = order_data.get(name)
            if not isinstance(value, str) or not value.strip():
                errors.append(ValidationError(name, value, "non-empty string"))
        side = order_data.get("side")
        if not isinstance(side, str) or side.lower() not in ("buy", "sell"):
            errors.append(ValidationError("side", side, "buy or sell"))
        return errors

    @staticmethod
    def is_valid(errors: list[ValidationError]) -> bool:
        return not errors
