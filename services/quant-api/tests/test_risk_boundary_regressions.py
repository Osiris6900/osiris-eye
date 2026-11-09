"""Fail-closed risk boundary tests: paper-only approvals. Refs #179."""
import pytest

from osiris_api.risk import RiskLimits, check_execution, check_order, pretrade_gate


CLEAR = dict(
    kill_switch_active=False,
    market_data_fresh=True,
    risk_service_available=True,
)


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        (float("nan"), "invalid_numeric_input"),
        (float("inf"), "invalid_numeric_input"),
        (float("-inf"), "invalid_numeric_input"),
        (True, "invalid_numeric_input"),
        ("10", "invalid_numeric_input"),
        (0, "invalid_order_or_position"),
        (-1, "invalid_order_or_position"),
    ],
)
def test_invalid_quantity_never_approved(bad, reason):
    allowed, actual = check_order(
        quantity=bad, price=100, current_position_notional=0, daily_pnl=0,
    )
    assert (allowed, actual) == (False, reason)


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        (float("nan"), "invalid_numeric_input"),
        (float("inf"), "invalid_numeric_input"),
        (True, "invalid_numeric_input"),
        ("100", "invalid_numeric_input"),
        (0, "invalid_order_or_position"),
        (-1, "invalid_order_or_position"),
    ],
)
def test_invalid_price_never_approved(bad, reason):
    allowed, actual = check_order(
        quantity=1, price=bad, current_position_notional=0, daily_pnl=0,
    )
    assert (allowed, actual) == (False, reason)


def test_overflowing_notional_rejected():
    allowed, reason = check_order(
        quantity=1e308, price=1e308, current_position_notional=0, daily_pnl=0,
    )
    assert (allowed, reason) == (False, "invalid_numeric_input")


def test_negative_position_notional_rejected():
    allowed, reason = check_order(
        quantity=1, price=100, current_position_notional=-0.01, daily_pnl=0,
    )
    assert (allowed, reason) == (False, "invalid_order_or_position")


@pytest.mark.parametrize(
    "gate",
    [
        {},
        {"kill_switch_active": False},
        {"kill_switch_active": False, "market_data_fresh": True},
        {"kill_switch_active": False, "risk_service_available": True},
        {"market_data_fresh": True, "risk_service_available": True},
        {"kill_switch_active": "false", "market_data_fresh": "true", "risk_service_available": "yes"},
        {"kill_switch_active": 0, "market_data_fresh": 1, "risk_service_available": 1},
        {"kill_switch_active": None, "market_data_fresh": None, "risk_service_available": None},
    ],
)
def test_missing_pretrade_evidence_fails_closed(gate):
    allowed, reason = pretrade_gate(**gate)
    assert allowed is False
    assert reason in {
        "risk_service_unavailable",
        "kill_switch_active",
        "stale_market_data",
    }


def test_stringly_typed_controls_do_not_clear_the_gate():
    allowed, reason = pretrade_gate(
        kill_switch_active="false",
        market_data_fresh="true",
        risk_service_available="true",
    )
    assert (allowed, reason) == (False, "risk_service_unavailable")


def test_paper_gate_cannot_bypass_stale_feed():
    allowed, reason, limits_evaluated = check_execution(
        side="BUY", quantity=1, price=10, current_position_notional=0,
        current_gross_exposure=0, daily_pnl=0,
        kill_switch_active=False, market_data_fresh=False,
        risk_service_available=True,
    )
    assert (allowed, reason, limits_evaluated) == (False, "stale_market_data", False)


def test_risk_service_outage_blocks_before_limits():
    allowed, reason, limits_evaluated = check_execution(
        side="BUY", quantity=1, price=10, current_position_notional=0,
        current_gross_exposure=0, daily_pnl=0,
        kill_switch_active=False, market_data_fresh=True,
        risk_service_available=False,
    )
    assert (allowed, reason, limits_evaluated) == (False, "risk_service_unavailable", False)


@pytest.mark.parametrize("gross_limit", [float("nan"), float("inf"), 0, -1, True])
def test_invalid_gross_exposure_limit_fails_closed(gross_limit):
    allowed, reason, limits_evaluated = check_execution(
        side="BUY", quantity=1, price=10, current_position_notional=0,
        current_gross_exposure=0, daily_pnl=0,
        limits=RiskLimits(max_gross_exposure=gross_limit),
        **CLEAR,
    )
    assert (allowed, reason, limits_evaluated) == (False, "invalid_risk_limits", True)


@pytest.mark.parametrize("gross", [float("nan"), True, "0", -1])
def test_invalid_gross_exposure_input_fails_closed(gross):
    allowed, reason, limits_evaluated = check_execution(
        side="BUY", quantity=1, price=10, current_position_notional=0,
        current_gross_exposure=gross, daily_pnl=0,
        **CLEAR,
    )
    assert allowed is False
    assert limits_evaluated is True
    assert reason in {"invalid_numeric_input", "invalid_order_or_position"}


@pytest.mark.parametrize("side", ["HOLD", "", None, True, "buy "])
def test_invalid_side_fails_closed(side):
    allowed, reason, limits_evaluated = check_execution(
        side=side, quantity=1, price=10, current_position_notional=10,
        current_gross_exposure=10, daily_pnl=0,
        **CLEAR,
    )
    assert (allowed, reason, limits_evaluated) == (False, "invalid_order_or_position", True)


def test_sell_larger_than_position_fails_closed():
    allowed, reason, limits_evaluated = check_execution(
        side="SELL", quantity=2, price=10, current_position_notional=10,
        current_gross_exposure=10, daily_pnl=0,
        **CLEAR,
    )
    assert (allowed, reason, limits_evaluated) == (False, "invalid_order_or_position", True)


def test_execution_overflow_never_approves():
    allowed, reason, limits_evaluated = check_execution(
        side="BUY", quantity=1e308, price=1e308, current_position_notional=0,
        current_gross_exposure=0, daily_pnl=0,
        **CLEAR,
    )
    assert (allowed, reason, limits_evaluated) == (False, "invalid_numeric_input", True)


def test_clear_gate_approves_simulation_only():
    allowed, reason, limits_evaluated = check_execution(
        side="buy", quantity=1, price=10, current_position_notional=0,
        current_gross_exposure=0, daily_pnl=0,
        **CLEAR,
    )
    assert (allowed, reason, limits_evaluated) == (True, "approved_for_simulation_only", True)
    order_allowed, order_reason = check_order(
        quantity=1, price=10, current_position_notional=0, daily_pnl=0,
    )
    assert (order_allowed, order_reason) == (True, "approved_for_simulation_only")
