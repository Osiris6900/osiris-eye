"""Hand-calculated scenarios and fail-closed contracts for microstructure mechanics."""
from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from osiris_api.market_mechanics_microstructure import CATALOG, router


@pytest.fixture
def client():
    application = FastAPI()
    application.include_router(router)
    return TestClient(application)


def scenario(index):
    return deepcopy(CATALOG[index]["sample_input"])


def post(client, index, payload=None):
    return client.post(CATALOG[index]["endpoint"], json=payload or scenario(index))


@pytest.mark.parametrize("index", range(5))
def test_catalog_examples_are_executable_research_scenarios(client, index):
    response = post(client, index)
    assert response.status_code == 200
    result = response.json()
    assert result["research_only"] is True
    assert result["execution_enabled"] is False
    assert result["limitations"] == CATALOG[index]["limitations"]
    assert result["evidence_strength"] == CATALOG[index]["evidence_strength"]


def test_signed_dealer_inventory_drives_gex_not_option_type(client):
    payload = scenario(0)
    result = post(client, 0, payload).json()
    # .02 gamma * -10 contracts * 100 multiplier * 100^2 spot * .01.
    assert result["net_gex_per_1pct_move"] == -2000
    assert result["hedge_notional_change_per_1pct_move"] == 2000
    assert result["gamma_regime"] == "short_gamma_amplifying"
    payload["options"][0]["option_type"] = "put"
    assert post(client, 0, payload).json()["net_gex_per_1pct_move"] == -2000
    payload["options"][0]["dealer_contracts"] = 10
    result = post(client, 0, payload).json()
    assert result["net_gex_per_1pct_move"] == 2000
    assert result["gamma_regime"] == "long_gamma_dampening"
    assert result["pinning_forecast"] is None


def test_missing_dealer_position_and_duplicate_contracts_rejected(client):
    payload = scenario(0)
    del payload["options"][0]["dealer_contracts"]
    assert post(client, 0, payload).status_code == 422
    payload = scenario(0)
    payload["options"].append(deepcopy(payload["options"][0]))
    assert post(client, 0, payload).status_code == 422


def test_gex_aggregation_can_be_neutral_despite_large_gross_exposure(client):
    payload = scenario(0)
    second = deepcopy(payload["options"][0])
    second.update(option_type="put", dealer_contracts=10)
    payload["options"].append(second)
    result = post(client, 0, payload).json()
    assert result["net_gex_per_1pct_move"] == 0
    assert result["gross_gex_per_1pct_move"] == 4000
    assert result["by_strike"] == [{"strike": 100, "gex_per_1pct_move": 0}]
    assert result["gamma_regime"] == "neutral"


def test_max_pain_reports_ties_without_price_target(client):
    payload = scenario(1)
    result = post(client, 1, payload).json()
    assert result["max_pain_candidates"] == [100]
    assert result["minimum_intrinsic_payout"] == 10000
    assert [row["intrinsic_payout"] for row in result["settlement_payouts"]] == [12500, 10000, 12500]
    assert result["price_target"] is None
    payload["chain"] = [
        {"strike": 90, "call_open_interest": 1, "put_open_interest": 0},
        {"strike": 110, "call_open_interest": 0, "put_open_interest": 1},
    ]
    assert post(client, 1, payload).json()["max_pain_candidates"] == [90, 110]


def test_zero_open_interest_is_no_information_not_universal_max_pain(client):
    payload = scenario(1)
    for row in payload["chain"]:
        row.update(call_open_interest=0, put_open_interest=0)
    result = post(client, 1, payload).json()
    assert result["information_available"] is False
    assert result["max_pain_candidates"] == []
    assert result["minimum_intrinsic_payout"] is None


def test_liquidity_sweep_uses_visible_depth_and_reports_shortfall(client):
    payload = scenario(2)
    payload["asks"].reverse()  # Sorting cannot depend on vendor row order.
    result = post(client, 2, payload).json()
    assert result["filled_quantity"] == 10
    assert result["unfilled_quantity"] == 5
    assert result["filled_vwap"] == 103
    assert result["slippage_from_best_bps"] == pytest.approx((103 / 101 - 1) * 10000)
    assert result["max_visible_gap_bps"] == pytest.approx(4 / 101 * 10000)
    assert result["depth_exhausted"] is True
    assert result["worst_fill_price"] == 105


def test_sell_sweep_sign_and_partial_last_level(client):
    payload = scenario(2)
    payload.update(side="sell", order_quantity=7,
                   bids=[{"price": 99, "quantity": 5}, {"price": 95, "quantity": 5}])
    result = post(client, 2, payload).json()
    assert result["filled_quantity"] == 7
    assert result["unfilled_quantity"] == 0
    assert result["filled_vwap"] == pytest.approx((99 * 5 + 95 * 2) / 7)
    assert result["slippage_from_best_bps"] > 0
    assert result["depth_exhausted"] is False


def test_trade_flow_imbalance_preserves_unknown_volume(client):
    result = post(client, 3).json()
    assert result["imbalance"] == .5
    assert result["classified_fraction"] == pytest.approx(100 / 120)
    assert result["unknown_volume"] == 20
    assert result["pressure"] == "buy"
    result = post(client, 3, {"trades": [{"aggressor_side": "unknown", "quantity": 10}]}).json()
    assert result["imbalance"] is None
    assert result["pressure"] == "unclassified"
    assert result["classified_fraction"] == 0


def test_dark_pool_share_vwap_and_no_inferred_direction(client):
    result = post(client, 4).json()
    assert result["dark_pool_volume"] == 150
    assert result["consolidated_volume_share"] == .15
    assert result["dark_pool_vwap"] == pytest.approx(15100 / 150)
    assert result["largest_print_volume_share"] == pytest.approx(2 / 3)
    assert result["inferred_direction"] == "unknown"
    assert result["institutional_intent"] == "unknown"
    payload = scenario(4)
    payload["dark_pool_prints"] = []
    result = post(client, 4, payload).json()
    assert result["dark_pool_vwap"] is None
    assert result["consolidated_volume_share"] == 0


@pytest.mark.parametrize(("index", "change"), [
    (0, {"spot": "100"}),
    (0, {"expiry": "2026-02-30"}),
    (0, {"expiry": "20261218"}),
    (0, {"spot": -1}),
    (0, {"spot": True}),
    (1, {"hours_to_expiry": -1}),
    (1, {"chain": [{"strike": 100, "call_open_interest": -1, "put_open_interest": 0}]}),
    (2, {"bids": [{"price": 105, "quantity": 1}]}),
    (2, {"order_quantity": 0}),
    (3, {"trades": [{"aggressor_side": "bid", "quantity": 1}]}),
    (3, {"trades": []}),
    (4, {"consolidated_volume": 100}),
    (4, {"reference_price": 0}),
])
def test_invalid_scenarios_return_422(client, index, change):
    payload = scenario(index)
    payload.update(change)
    assert post(client, index, payload).status_code == 422


@pytest.mark.parametrize("index", range(5))
def test_unknown_fields_and_oversized_batches_are_rejected(client, index):
    payload = scenario(index)
    payload["live_execution"] = True
    assert post(client, index, payload).status_code == 422
    field = ["options", "chain", "asks", "trades", "dark_pool_prints"][index]
    payload = scenario(index)
    payload[field] = payload[field] * 1001
    assert post(client, index, payload).status_code == 422


def test_nonfinite_strings_and_output_overflow_are_rejected(client):
    for value in ("NaN", "Infinity", "-Infinity"):
        payload = scenario(0)
        payload["spot"] = value
        assert post(client, 0, payload).status_code == 422
    payload = scenario(4)
    payload["reference_price"] = 5e-324
    assert post(client, 4, payload).status_code == 422
