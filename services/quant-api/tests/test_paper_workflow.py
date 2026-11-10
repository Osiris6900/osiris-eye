from osiris_api.paper_workflow import evaluate_paper_workflow


def test_paper_workflow_backtests_and_risk_approves_without_execution():
    result = evaluate_paper_workflow(
        symbol="btc-usd",
        signals=[0.0, 0.5, 0.5],
        asset_returns=[0.0, 0.01, -0.02],
        quantity=1,
        price=100,
        kill_switch_active=False,
        market_data_fresh=True,
        risk_service_available=True,
        current_gross_exposure=0.0,
    )
    assert result["symbol"] == "BTC-USD"
    assert result["backtest"]["periods"] == 2
    assert result["risk"]["approved_for_paper"] is True
    assert result["paper_order_intent"]["status"] == "PAPER_INTENT_APPROVED"
    assert result["execution"]["broker_order_submitted"] is False
    assert result["execution"]["live_execution"] is False


def test_paper_workflow_blocks_order_over_risk_limit():
    result = evaluate_paper_workflow(
        symbol="ETH-USD",
        signals=[0.0, 1.0],
        asset_returns=[0.0, 0.01],
        quantity=200,
        price=100,
        kill_switch_active=False,
        market_data_fresh=True,
        risk_service_available=True,
        current_gross_exposure=0.0,
    )
    assert result["risk"]["approved_for_paper"] is False
    assert result["risk"]["reason"] == "max_order_notional_exceeded"
    assert result["paper_order_intent"]["status"] == "BLOCKED_BY_RISK"
    assert result["execution"]["broker_order_submitted"] is False


def test_paper_workflow_rejects_non_string_symbol():
    import pytest

    for symbol in (None, 123, True, ["BTC-USD"]):
        with pytest.raises(ValueError, match="invalid_symbol"):
            evaluate_paper_workflow(
                symbol=symbol,
                signals=[0.0, 0.5],
                asset_returns=[0.0, 0.01],
                quantity=1,
                price=100,
            )
