import math

import pytest

from osiris_api.research import (
    estimated_one_way_cost,
    expiry_gamma_feature,
    implied_minus_forecast_vol,
    lagged_correlation,
    market_regime,
    relative_iv_zscore,
)
from osiris_api.risk import RiskLimits, check_order


def test_iv_zscore():
    assert relative_iv_zscore(0.3, [0.1, 0.2, 0.3]) == pytest.approx(1.22474487)


def test_zero_dispersion():
    assert relative_iv_zscore(0.2, [0.2, 0.2]) == 0


@pytest.mark.parametrize("target, peers", [(-0.1, [0.2, 0.3]), (0.2, []), (0.2, [math.nan])])
def test_iv_rejects_invalid_values(target, peers):
    with pytest.raises(ValueError):
        relative_iv_zscore(target, peers)


def test_liquidity_cost():
    assert estimated_one_way_cost(0.02, 0.01) == pytest.approx(0.03)


def test_liquidity_rejects_negative_cost():
    with pytest.raises(ValueError):
        estimated_one_way_cost(-0.01, 0.02)


def test_expiry_gamma():
    assert expiry_gamma_feature(-4, 4) == 2
    assert expiry_gamma_feature(4, 0) == 4


def test_volatility_spread():
    assert implied_minus_forecast_vol(0.25, 0.2) == pytest.approx(0.05)


def test_lagged_correlation():
    assert lagged_correlation([1, 2, 3, 4], [2, 3, 4, 5]) == pytest.approx(1)
    assert lagged_correlation([1, 2, 3, 4], [0, 1, 2, 3], lag=1) == pytest.approx(1)


def test_lagged_correlation_rejects_bad_lag():
    with pytest.raises(ValueError):
        lagged_correlation([1, 2, 3], [1, 2, 3], lag=3)


def test_regime_thresholds():
    assert market_regime(0.2, 5)["risk_restricted"] is False
    assert market_regime(0.4, 5)["risk_restricted"] is True
    assert market_regime(0.2, 20)["risk_restricted"] is True


def test_risk_allows_small_simulated_order():
    allowed, reason = check_order(
        quantity=2, price=100, current_position_notional=0, daily_pnl=0
    )
    assert allowed is True
    assert reason == "approved_for_simulation_only"


def test_risk_rejects_order_limit():
    allowed, reason = check_order(
        quantity=200, price=100, current_position_notional=0, daily_pnl=0
    )
    assert allowed is False
    assert reason == "max_order_notional_exceeded"


def test_risk_rejects_position_limit():
    allowed, reason = check_order(
        quantity=10, price=100, current_position_notional=49_500, daily_pnl=0
    )
    assert allowed is False
    assert reason == "max_position_notional_exceeded"


def test_risk_rejects_daily_loss_limit():
    allowed, reason = check_order(
        quantity=1, price=100, current_position_notional=0, daily_pnl=-1_000
    )
    assert allowed is False
    assert reason == "daily_loss_limit_reached"


def test_risk_fails_closed_on_non_finite_input():
    allowed, reason = check_order(
        quantity=math.inf, price=100, current_position_notional=0, daily_pnl=0
    )
    assert allowed is False
    assert reason == "invalid_numeric_input"


def test_risk_rejects_invalid_limits():
    allowed, reason = check_order(
        quantity=1,
        price=100,
        current_position_notional=0,
        daily_pnl=0,
        limits=RiskLimits(max_order_notional=0),
    )
    assert allowed is False
    assert reason == "invalid_risk_limits"
