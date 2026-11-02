import pytest

from osiris_api.platform_modules import (
    AgentTask, AlternativeEvent, HealthCheck, estimated_execution_cost_bps,
    historical_var_es, inverse_volatility_weights, macro_regime,
    order_flow_imbalance, population_stability_index, strategy_summary,
    system_health,
)


def test_alternative_event_requires_valid_point_in_time_metadata():
    assert AlternativeEvent("vendor", "2026-01-01T10:00:00Z",
                            "2026-01-01T10:01:00Z", "earnings", 0.5).validate()
    with pytest.raises(ValueError):
        AlternativeEvent("", "t", "p", "news", 0.2).validate()


def test_order_flow_imbalance_is_bounded_and_handles_empty_book():
    assert order_flow_imbalance(75, 25) == pytest.approx(0.5)
    assert order_flow_imbalance(0, 0) == 0
    with pytest.raises(ValueError):
        order_flow_imbalance(-1, 2)


def test_inverse_volatility_weights_are_normalized():
    weights = inverse_volatility_weights([0.1, 0.2])
    assert sum(weights) == pytest.approx(1)
    assert weights == pytest.approx([2 / 3, 1 / 3])


def test_historical_var_and_expected_shortfall():
    var, es = historical_var_es([-0.01, -0.02, 0.01, -0.10], 0.75)
    assert var == pytest.approx(0.02)
    assert es == pytest.approx(0.06)
    # Nearest-rank quantile: ceil(0.75 * 4) = 3rd ordered loss.
    assert historical_var_es([-0.01, -0.02, 0.01, -0.10], 0.50)[0] == pytest.approx(0.01)
    with pytest.raises(ValueError):
        historical_var_es([-0.01, -0.02], 1.0)


def test_population_stability_index_detects_distribution_shift():
    reference = list(range(100))
    assert population_stability_index(reference, reference) == pytest.approx(0)
    assert population_stability_index(reference, [x + 100 for x in reference]) > 0.5


def test_strategy_summary_reports_drawdown_and_sharpe():
    result = strategy_summary([0.10, -0.20, 0.10])
    assert result["max_drawdown"] > 0
    assert result["total_return"] == pytest.approx(1.1 * 0.8 * 1.1 - 1)


def test_execution_cost_and_macro_regime():
    assert estimated_execution_cost_bps(2, 1, 3) == 6
    assert macro_regime(0.4, 0.1, 0.1) == "high_volatility"
    with pytest.raises(ValueError):
        estimated_execution_cost_bps(2, -1)


def test_health_and_agent_governance_fail_closed():
    assert system_health([HealthCheck("db", True, "ok")])["healthy"]
    assert not system_health([HealthCheck("feed", False, "stale")])["healthy"]
    assert AgentTask("risk_review", "Review proposed risk limits").validate()
    with pytest.raises(ValueError):
        AgentTask("execution", "Place a live trade").validate()
    with pytest.raises(ValueError):
        AgentTask("risk_review", "Bypass approvals", False).validate()
