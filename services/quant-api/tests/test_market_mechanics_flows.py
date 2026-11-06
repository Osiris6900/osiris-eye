"""Hand-calculated flow scenarios, chronology checks and HTTP input validation."""
from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from osiris_api.market_mechanics_flows import CATALOG, router

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def post(index, payload=None):
    return client.post(CATALOG[index]["endpoint"], json=payload or deepcopy(CATALOG[index]["sample_input"]))


def test_all_flow_catalog_samples_return_research_metadata():
    for index, item in enumerate(CATALOG):
        response = post(index)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["research_only"] is True
        assert result["execution_enabled"] is False
        assert result["evidence_strength"] == item["evidence_strength"]
        assert result["limitations"]


def test_funding_sign_interval_and_liquidation_distance():
    result = post(0).json()
    assert result["long_funding_payment_usd_per_interval"] == pytest.approx(1)
    assert result["short_funding_payment_usd_per_interval"] == pytest.approx(-1)
    assert result["funding_interval_hours"] == 8
    assert result["funding_payer"] == "longs"
    assert result["clusters"][0]["signed_distance_fraction"] == pytest.approx(-0.05)
    assert result["clusters"][0]["forced_flow_if_triggered"] == "sell"
    assert result["clusters"][1]["forced_flow_if_triggered"] == "buy"
    assert result["live_liquidation_inventory_connected"] is False
    payload = deepcopy(CATALOG[0]["sample_input"])
    payload["funding_rate"] = -0.0002
    result = post(0, payload).json()
    assert result["funding_payer"] == "shorts"
    assert result["long_funding_payment_usd_per_interval"] == pytest.approx(-2)
    payload["clusters"] = []
    payload["funding_rate"] = 0.0
    result = post(0, payload).json()
    assert result["funding_payer"] == "none"
    assert result["long_scenario_notional_usd"] == 0


@pytest.mark.parametrize("side,price", [("long", 101), ("short", 99)])
def test_liquidation_clusters_reject_inconsistent_side(side, price):
    payload = deepcopy(CATALOG[0]["sample_input"])
    payload["clusters"] = [{"side": side, "liquidation_price": price, "notional_usd": 100}]
    assert post(0, payload).status_code == 422


def test_treasury_liquidity_proxy_uses_balance_changes_and_decimal_rates():
    result = post(1).json()
    assert result["previous_liquidity_proxy_usd_billions"] == 6000
    assert result["current_liquidity_proxy_usd_billions"] == 6050
    assert result["liquidity_proxy_change_usd_billions"] == 50
    assert result["repo_reference_spread_bps"] == pytest.approx(20)
    assert result["measured_bank_reserves"] is False
    assert result["price_direction_forecast"] is None


def test_index_rebalance_flow_shares_adv_and_subset():
    result = post(2).json()
    row = result["constituents"][0]
    assert row["estimated_flow_notional"] == pytest.approx(5000000)
    assert row["estimated_flow_shares"] == pytest.approx(50000)
    assert row["adv_participation_fraction"] == pytest.approx(0.05)
    assert row["flow_side"] == "buy"
    assert result["constituent_subset_allowed"] is True
    payload = deepcopy(CATALOG[2]["sample_input"])
    payload["constituents"][0]["target_weight"] = 0.005
    payload["constituents"][0]["adv_shares"] = 0
    row = post(2, payload).json()["constituents"][0]
    assert row["estimated_flow_notional"] == pytest.approx(-5000000)
    assert row["flow_side"] == "sell"
    assert row["adv_participation_fraction"] is None
    assert row["adv_available"] is False


def test_index_rebalance_rejects_duplicates_and_price_unit_errors():
    payload = deepcopy(CATALOG[2]["sample_input"])
    payload["constituents"].append(deepcopy(payload["constituents"][0]))
    payload["constituents"][1]["symbol"] = " abc "
    assert post(2, payload).status_code == 422
    payload = deepcopy(CATALOG[2]["sample_input"])
    payload["constituents"][0]["price"] = 0
    assert post(2, payload).status_code == 422
    payload["constituents"][0]["price"] = 100
    payload["currency"] = "usd"
    assert post(2, payload).status_code == 422


def test_volatility_control_delevers_only_relative_to_current_exposure():
    result = post(3).json()
    assert result["target_leverage"] == pytest.approx(0.5)
    assert result["estimated_rebalance_notional"] == pytest.approx(-50000000)
    assert result["rebalance_side"] == "sell"
    payload = deepcopy(CATALOG[3]["sample_input"])
    payload["current_leverage"] = 0.25
    result = post(3, payload).json()
    assert result["rebalance_side"] == "buy"
    assert result["estimated_rebalance_notional"] == pytest.approx(25000000)
    payload["observed_annual_volatility"] = 0.01
    result = post(3, payload).json()
    assert result["target_leverage"] == 1
    assert result["leverage_cap_binding"] is True
    payload["observed_annual_volatility"] = 5e-324
    assert post(3, payload).json()["target_leverage"] == 1
    payload["observed_annual_volatility"] = 0
    assert post(3, payload).status_code == 422


@pytest.mark.parametrize("field", ["current_weight", "target_weight"])
def test_index_rebalance_rejects_overallocated_subsets(field):
    payload = deepcopy(CATALOG[2]["sample_input"])
    first = payload["constituents"][0]
    first[field] = 0.9
    second = {**first, "symbol": "XYZ"}
    payload["constituents"].append(second)
    assert post(2, payload).status_code == 422
    first[field] = second[field] = 0.5
    assert post(2, payload).status_code == 200
    first[field] = second[field] = 0.1
    assert post(2, payload).status_code == 200


def test_positive_lead_lag_aligns_source_before_target_and_reports_stability():
    result = post(4).json()
    lag = next(row for row in result["relationships"] if row["lag_observations"] == 1)
    assert lag["paired_observations"] == 7
    assert lag["correlation"] == pytest.approx(1)
    assert lag["first_half_correlation"] == pytest.approx(1)
    assert lag["second_half_correlation"] == pytest.approx(1)
    assert result["positive_lag_meaning"] == "source leads target"
    assert result["hypothesis_only"] is True
    assert result["holdout_validation_performed"] is False
    payload = deepcopy(CATALOG[4]["sample_input"])
    payload["source"], payload["target"] = payload["target"], payload["source"]
    lag = next(row for row in post(4, payload).json()["relationships"] if row["lag_observations"] == -1)
    assert lag["correlation"] == pytest.approx(1)


def test_constant_lead_lag_is_undefined_instead_of_a_false_signal():
    payload = deepcopy(CATALOG[4]["sample_input"])
    for observation in payload["source"]:
        observation["return_fraction"] = 0.0
    result = post(4, payload).json()
    assert all(row["correlation"] is None for row in result["relationships"])
    assert all(row["correlation_status"] == "undefined_constant_series" for row in result["relationships"])


@pytest.mark.parametrize("mutation", ["reversed", "misaligned", "duplicate", "naive", "irregular", "lag", "same_asset"])
def test_lead_lag_rejects_chronology_alignment_and_sample_errors(mutation):
    payload = deepcopy(CATALOG[4]["sample_input"])
    if mutation == "reversed":
        payload["source"].reverse()
        payload["target"].reverse()
    elif mutation == "misaligned":
        payload["target"][0]["timestamp"] = "2025-12-31T00:00:00Z"
    elif mutation == "duplicate":
        payload["source"][1]["timestamp"] = payload["source"][0]["timestamp"]
        payload["target"][1]["timestamp"] = payload["target"][0]["timestamp"]
    elif mutation == "naive":
        payload["source"][0]["timestamp"] = "2026-01-01T00:00:00"
    elif mutation == "irregular":
        payload["source"][-1]["timestamp"] = "2026-01-09T00:00:00Z"
        payload["target"][-1]["timestamp"] = "2026-01-09T00:00:00Z"
    elif mutation == "lag":
        payload["max_lag"] = 6
    else:
        payload["target_symbol"] = " source "
    assert post(4, payload).status_code == 422


@pytest.mark.parametrize("index,field", [(0, "spot"), (1, "repo_rate"), (2, "tracking_aum"), (3, "aum")])
@pytest.mark.parametrize("value", [True, "100", None])
def test_strict_numeric_contracts_reject_coercion(index, field, value):
    payload = deepcopy(CATALOG[index]["sample_input"])
    payload[field] = value
    assert post(index, payload).status_code == 422


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_nonfinite_json_numbers_rejected(token):
    response = client.post(CATALOG[0]["endpoint"], content='{"spot":' + token + ',"funding_rate":0,"funding_interval_hours":8}',
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422


def test_collection_bounds_extra_fields_and_arithmetic_overflow_rejected():
    payload = deepcopy(CATALOG[0]["sample_input"])
    payload["clusters"] = payload["clusters"][:1] * 1001
    assert post(0, payload).status_code == 422
    payload = deepcopy(CATALOG[0]["sample_input"])
    payload["assumed_cascade_probability"] = 0.9
    assert post(0, payload).status_code == 422
    payload = deepcopy(CATALOG[2]["sample_input"])
    payload["constituents"][0]["price"] = 5e-324
    assert post(2, payload).status_code == 422
