"""Bound decimal work before parsing or fixed-point formatting of ledger values.

These are input/resource bounds, not financial risk budgets. Ordinary amounts
retain their exact Decimal representation; no rounding is introduced here.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

MAX_DECIMAL_CHARACTERS = 256
MAX_DECIMAL_DIGITS = 256
MAX_DECIMAL_EXPONENT = 1000


def validate_decimal_bounds(number: Decimal) -> Decimal:
    if not number.is_finite():
        raise ValueError("invalid_number")
    parts = number.as_tuple()
    if len(parts.digits) > MAX_DECIMAL_DIGITS or abs(parts.exponent) > MAX_DECIMAL_EXPONENT:
        raise ValueError("decimal_precision_out_of_range")
    return number


def bounded_decimal(value: Any, *, allow_float: bool = False) -> Decimal:
    accepted = (str, int, Decimal, float) if allow_float else (str, int, Decimal)
    if isinstance(value, bool) or not isinstance(value, accepted):
        # Ledger HTTP handlers deliberately map validation ValueError to 422.
        raise ValueError("invalid_number")  # noqa: TRY004
    if isinstance(value, Decimal):
        return validate_decimal_bounds(value)
    # Avoid converting a huge Python integer to a huge decimal string first.
    if isinstance(value, int) and value.bit_length() > MAX_DECIMAL_CHARACTERS * 4:
        raise ValueError("decimal_precision_out_of_range")
    text = str(value)
    if len(text) > MAX_DECIMAL_CHARACTERS:
        raise ValueError("decimal_precision_out_of_range")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("invalid_number") from exc
    return validate_decimal_bounds(number)
