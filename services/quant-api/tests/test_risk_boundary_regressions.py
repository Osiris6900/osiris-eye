"""Fail-closed risk boundary tests: paper-only approvals."""
import math
import pytest

from osiris_api.risk import RiskLimits, check_order, check_execution, pretrade_gate


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), True, "10", 0, -1])
def test_invalid_quantity_never_approved(bad):
    allowed, _ = check_order(quantity=bad, price=100, current_position_notional=0, daily_pnl=0)
    assert allowed is False


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "100", 0, -1])
def test_invalid_price_never_approved(bad):
    allowed, _ = check_order(quantity=1, price=bad, current_position_notional=0, daily_pnl=0)
    assert allowed is False


def test_overflowing_notional_rejected():
    allowed, reason = check_order(quantity=1e308, price=1e308,
                                  current_position_notional=0, daily_pnl=0)
    assert (allowed, reason) == (False, "invalid_numeric_input")


@pytest.mark.parametrize("gate", [
    {}, {"kill_switch_active": False},
    {"kill_switch_active": False, "market_data_fresh": True},
    {"kill_switch_active": False, "risk_service_available": True},
    {"market_data_fresh": True, "risk_service_available": True},
])
def test_missing_pretrade_evidence_fails_closed(gate):
    allowed, _ = pretrade_gate(**gate)
    assert allowed is False


def test_paper_gate_cannot_bypass_stale_feed():
    allowed, reason, limits_evaluated = check_execution(
        side="BUY", quantity=1, price=10, current_position_notional=0,
        current_gross_exposure=0, daily_pnl=0,
        kill_switch_active=False, market_data_fresh=False,
        risk_service_available=True,
    )
    assert (allowed, reason, limits_evaluated) == (False, "stale_market_data", False)


def test_invalid_gross_exposure_limit_fails_closed():
    allowed, reason, _ = check_execution(
        side="BUY", quantity=1, price=10, current_position_notional=0,
        current_gross_exposure=0, daily_pnl=0,
        limits=RiskLimits(max_gross_exposure=float("nan")),
        kill_switch_active=False, market_data_fresh=True,
        risk_service_available=True,
    )
    assert (allowed, reason) == (False, "invalid_risk_limits")
