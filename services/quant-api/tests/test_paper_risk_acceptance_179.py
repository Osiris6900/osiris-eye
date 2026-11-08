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
