"""Boundary and adversarial regression checks for paper-only risk decisions."""
import math
import pytest

from osiris_api.risk import RiskLimits, check_order


BASE = dict(quantity=1.0, price=100.0, current_position_notional=0.0, daily_pnl=0.0)


def decision(**changes):
    request = {**BASE, **changes}
    return check_order(**request)


@pytest.mark.parametrize("field,value", [
    ("quantity", float("nan")), ("price", float("inf")),
    ("current_position_notional", -float("inf")), ("daily_pnl", float("nan")),
    ("quantity", True), ("price", "100"),
])
def test_untrusted_numeric_inputs_fail_closed(field, value):
    assert decision(**{field: value}) == (False, "invalid_numeric_input")


def test_order_and_position_limits_include_exact_boundary():
    limits = RiskLimits(max_order_notional=100, max_position_notional=200, max_daily_loss=50)
    assert check_order(**BASE, limits=limits)[0] is True
    assert check_order(**{**BASE, "current_position_notional": 100}, limits=limits)[0] is True
    assert check_order(**{**BASE, "current_position_notional": 101}, limits=limits) == (
        False, "max_position_notional_exceeded"
    )


def test_daily_loss_boundary_is_fail_closed():
    assert decision(daily_pnl=-1000) == (False, "daily_loss_limit_reached")
    assert decision(daily_pnl=-999.99)[0] is True


def test_notional_overflow_never_approves():
    assert decision(quantity=1e308, price=1e308) == (False, "invalid_numeric_input")


@pytest.mark.parametrize("limits", [
    RiskLimits(max_order_notional=float("nan")),
    RiskLimits(max_position_notional=-1),
    RiskLimits(max_daily_loss=-1),
])
def test_invalid_limits_fail_closed(limits):
    assert check_order(**BASE, limits=limits) == (False, "invalid_risk_limits")
