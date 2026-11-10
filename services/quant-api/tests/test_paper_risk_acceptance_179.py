"""Regression checks for the research-to-paper risk boundary (#179)."""
import pytest

from osiris_api.paper_workflow import evaluate_paper_workflow
from osiris_api.risk import RiskLimits


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        ({"quantity": 101, "price": 100}, "max_order_notional_exceeded"),
        ({"current_position_notional": 49_950}, "max_position_notional_exceeded"),
        ({"daily_pnl": -1_000}, "daily_loss_limit_reached"),
        ({"quantity": 0}, "invalid_order_or_position"),
        ({"price": -1}, "invalid_order_or_position"),
        ({"limits": RiskLimits(max_order_notional=0)}, "invalid_risk_limits"),
    ],
)
def test_rejected_proposal_never_approves_paper_intent(overrides, expected_reason):
    inputs = dict(
        symbol="BTC-USD", signals=[0.0, 1.0],
        asset_returns=[0.0, 0.01], quantity=1, price=100,
        enforce_execution_controls=True,
        kill_switch_active=False, market_data_fresh=True,
        risk_service_available=True, current_gross_exposure=0.0,
    )
    inputs.update(overrides)
    result = evaluate_paper_workflow(**inputs)
    assert result["risk"]["approved_for_paper"] is False
    assert result["risk"]["reason"] == expected_reason
    assert result["paper_order_intent"]["status"] == "BLOCKED_BY_RISK"
    assert result["execution"]["broker_order_submitted"] is False
    assert result["execution"]["live_execution"] is False


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), True, "100"])
def test_invalid_quantity_fails_closed_before_paper_intent(bad_value):
    with pytest.raises(ValueError, match="invalid_numeric_input"):
        evaluate_paper_workflow(
            symbol="BTC-USD", signals=[0.0, 1.0],
            asset_returns=[0.0, 0.01], quantity=bad_value, price=100,
        )


def _controlled(**overrides):
    inputs = dict(
        symbol="BTC-USD", signals=[0.0, 1.0], asset_returns=[0.0, 0.01],
        quantity=1, price=100, enforce_execution_controls=True,
        kill_switch_active=False, market_data_fresh=True,
        risk_service_available=True, current_gross_exposure=0.0,
    )
    inputs.update(overrides)
    return evaluate_paper_workflow(**inputs)


def test_execution_controls_default_fail_closed_and_submit_nothing():
    result = evaluate_paper_workflow(
        symbol="BTC-USD", signals=[0.0, 1.0], asset_returns=[0.0, 0.01],
        quantity=1, price=100, enforce_execution_controls=True,
    )
    assert result["risk"]["approved_for_paper"] is False
    assert result["risk"]["reason"] == "risk_service_unavailable"
    assert result["risk"]["limits_evaluated"] is False
    assert result["risk"]["live_execution"] is False
    assert result["paper_order_intent"]["status"] == "BLOCKED_BY_RISK"
    assert result["execution"]["broker_order_submitted"] is False
    assert result["execution"]["live_execution"] is False


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"kill_switch_active": True, "market_data_fresh": False}, "kill_switch_active"),
        ({"market_data_fresh": False}, "stale_market_data"),
        ({"risk_service_available": False}, "risk_service_unavailable"),
        ({"current_gross_exposure": None}, "exposure_input_missing"),
        (
            {"current_position_notional": 49_950, "current_gross_exposure": 49_950},
            "max_position_notional_exceeded",
        ),
        (
            {
                "current_gross_exposure": 99_950,
                "limits": RiskLimits(max_gross_exposure=100_000),
            },
            "exposure_budget_exceeded",
        ),
    ],
)
def test_pretrade_and_exposure_gates_block_before_any_fill(overrides, reason):
    result = _controlled(**overrides)
    assert result["risk"]["approved_for_paper"] is False
    assert result["risk"]["reason"] == reason
    if reason in {"kill_switch_active", "stale_market_data", "risk_service_unavailable",
                  "exposure_input_missing"}:
        assert result["risk"]["limits_evaluated"] is False
    else:
        assert result["risk"]["limits_evaluated"] is True
    assert result["paper_order_intent"]["status"] == "BLOCKED_BY_RISK"
    assert result["execution"]["live_execution"] is False
    assert result["execution"]["broker_order_submitted"] is False


def test_healthy_controls_approve_paper_intent_without_live_execution():
    result = _controlled()
    assert result["risk"] == {
        "approved_for_paper": True,
        "reason": "approved_for_simulation_only",
        "order_notional": 100.0,
        "limits_evaluated": True,
        "live_execution": False,
    }
    assert result["paper_order_intent"]["status"] == "PAPER_INTENT_APPROVED"
    assert result["execution"]["broker_order_submitted"] is False
    assert result["execution"]["live_execution"] is False

@pytest.mark.parametrize(
    "controls",
    [
        {},
        {"enforce_execution_controls": False},
        {"enforce_execution_controls": 0},
        {"enforce_execution_controls": "true"},
        {
            "enforce_execution_controls": False,
            "kill_switch_active": False,
            "market_data_fresh": True,
            "risk_service_available": True,
            "current_gross_exposure": 0.0,
        },
    ],
)
def test_paper_workflow_cannot_approve_without_enforced_controls(controls):
    result = evaluate_paper_workflow(
        symbol="BTC-USD", signals=[0.0, 1.0],
        asset_returns=[0.0, 0.01], quantity=1, price=100,
        **controls,
    )
    assert result["risk"]["approved_for_paper"] is False
    expected = "risk_service_unavailable" if controls.get(
        "enforce_execution_controls", True
    ) is True else "execution_controls_required"
    assert result["risk"]["reason"] == expected
    assert result["risk"]["limits_evaluated"] is False
    assert result["paper_order_intent"]["status"] == "BLOCKED_BY_RISK"
    assert result["execution"] == {
        "mode": "paper_only", "broker_order_submitted": False,
        "live_execution": False,
    }


def test_paper_workflow_hold_signal_never_approves_intent():
    result = evaluate_paper_workflow(
        symbol="BTC-USD", signals=[0.0, 0.0],
        asset_returns=[0.0, 0.01], quantity=1, price=100,
        enforce_execution_controls=True, kill_switch_active=False,
        market_data_fresh=True, risk_service_available=True,
        current_gross_exposure=0.0,
    )
    assert result["risk"]["approved_for_paper"] is False
    assert result["risk"]["reason"] == "hold_signal_no_order"
    assert result["paper_order_intent"]["status"] == "BLOCKED_BY_RISK"
