"""Economic constraints, numeric extremes and authorization for allocation previews."""

import copy
import json
import math
import random
from decimal import localcontext

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from osiris_api.control_plane.api import router
from osiris_api.phase3_systems import portfolio_optimizer
from osiris_api.platform_modules import inverse_volatility_weights
from osiris_api.portfolio_construction import (
    MAX_ASSETS,
    MAX_SCENARIOS,
    allocation_preview,
    capped_inverse_volatility_weights,
)

ASSETS = [{"symbol": "A", "volatility": 0.1}, {"symbol": "B", "volatility": 0.2}]


def test_known_capped_solution_redistributes_only_to_assets_with_capacity():
    weights = capped_inverse_volatility_weights([0.1, 0.2, 0.4], [0.2, 0.7, 1.0])
    assert weights == pytest.approx([0.2, 8 / 15, 4 / 15])
    excluded = capped_inverse_volatility_weights([5e-324, 0.2, 0.4], [0, 1, 1])
    assert excluded == pytest.approx([0, 2 / 3, 1 / 3])


@pytest.mark.parametrize(
    "vols,caps,expected",
    [
        ([5e-324, 0.2], [1, 1], [1, 0]),
        ([5e-324, 1e308], [0.25, 1], [0.25, 0.75]),
        ([1e308, 1e308], [1, 1], [0.5, 0.5]),
        ([5e-324, 5e-324, 1e308], [0.2, 0.3, 1], [0.2, 0.3, 0.5]),
    ],
)
def test_full_float_range_stays_finite_across_multiple_cap_rounds(vols, caps, expected):
    result = capped_inverse_volatility_weights(vols, caps)
    assert result == pytest.approx(expected)
    assert all(math.isfinite(value) for value in result)
    json.dumps(result, allow_nan=False)


def test_randomized_budget_caps_and_inverse_volatility_conditions():
    rng = random.Random(413)
    for _ in range(200):
        count = rng.randint(1, MAX_ASSETS)
        vols = [10 ** rng.uniform(-300, 300) for _ in range(count)]
        caps = [rng.choice([0.0, rng.random()]) for _ in range(count)]
        budget = rng.random()
        weights = capped_inverse_volatility_weights(vols, caps, budget)
        assert all(0 <= value <= cap and math.isfinite(value) for value, cap in zip(weights, caps))
        assert math.fsum(weights) <= budget
        assert math.fsum(weights) == pytest.approx(min(budget, math.fsum(caps)), abs=1e-12)
    vols, caps = [0.1, 0.2, 0.3, 0.4], [0.2, 1, 1, 1]
    weights = capped_inverse_volatility_weights(vols, caps)
    assert weights[0] == 0.2
    assert [weights[i] * vols[i] for i in (1, 2, 3)] == pytest.approx([weights[1] * vols[1]] * 3)


def test_named_caps_and_cash_reserve_preserve_funding_and_inputs():
    assets = [
        {"symbol": "A", "volatility": 0.1, "max_weight": 0.2},
        {"symbol": "B", "volatility": 0.2, "max_weight": 0.9},
    ]
    original = copy.deepcopy(assets)
    report = allocation_preview(assets, max_weight=0.5, cash_reserve=0.1)
    assert [row["weight"] for row in report["allocations"]] == pytest.approx([0.2, 0.5])
    assert report["cash_weight"] == pytest.approx(0.3)
    assert report["unallocated_due_to_caps"] == pytest.approx(0.2)
    assert report["cash_weight"] + report["invested_weight"] == pytest.approx(1)
    assert report["scenario_policy"] == "not_evaluated"
    assert assets == original
    assert allocation_preview(assets, max_weight=0.5, cash_reserve=0.1) == report


@pytest.mark.parametrize("reserve", [0, 0.1, 0.3, 0.7, 1, 5e-324])
def test_cash_floor_is_not_violated_by_binary_roundoff(reserve):
    report = allocation_preview(ASSETS, cash_reserve=reserve)
    assert report["cash_weight"] >= reserve
    assert report["invested_weight"] + report["cash_weight"] == pytest.approx(1)


def test_exclusions_and_entirely_cash_portfolios_are_explicit():
    report = allocation_preview([{**row, "max_weight": 0} for row in ASSETS])
    assert report["invested_weight"] == 0
    assert report["cash_weight"] == 1
    assert report["unallocated_due_to_caps"] == 1
    assert report["scenario_policy"] == "not_evaluated"
    assert allocation_preview(ASSETS, cash_reserve=1)["cash_weight"] == 1


def test_stress_limits_apply_to_the_cash_aware_portfolio_without_false_boundary_breaches():
    assets = [{**row, "volatility": 0.2} for row in ASSETS]
    scenarios = [
        {"id": "crash", "shocks": {"A": -0.4, "B": -0.2}},
        {"id": "rally", "shocks": {"A": 0.4, "B": 0.2}},
    ]
    options = {"cash_reserve": 0.5, "scenarios": scenarios, "max_scenario_loss": 0.15}
    report = allocation_preview(assets, **options)
    crash, rally = report["scenarios"]
    assert crash["contributions"] == {"A": -0.1, "B": -0.05}
    assert crash["portfolio_return"] == -0.15
    assert crash["loss"] == 0.15
    assert rally["loss"] == 0
    assert report["scenario_policy"] == "pass"
    assert report["cash_scenario_return_assumption"] == 0
    breached = allocation_preview(assets, **{**options, "max_scenario_loss": 0.149999999999})
    assert breached["scenario_policy"] == "breach"
    assert breached["breached_scenarios"] == ["crash"]
    for key in (
        "future_drawdown_guaranteed",
        "risk_approval_granted",
        "live_execution",
        "broker_order_submitted",
        "optimality_claim",
    ):
        assert report[key] is breached[key] is False
    assert report["input_sha256"] != breached["input_sha256"]
    with localcontext() as context:
        context.prec = 3
        context.Emax = 1
        context.Emin = -1
        assert allocation_preview(assets, **options) == report


@pytest.mark.parametrize(
    "value", [True, False, "0.1", None, float("nan"), float("inf"), -float("inf"), 10**400]
)
def test_invalid_numeric_inputs_fail_closed(value):
    for changes in (
        {"max_weight": value},
        {"cash_reserve": value},
        {"assets": [{"symbol": "A", "volatility": value}]},
        {"assets": [{"symbol": "A", "volatility": 0.2, "max_weight": value}]},
    ):
        with pytest.raises((ValueError, TypeError)):
            allocation_preview(**{"assets": ASSETS, **changes})
    with pytest.raises((ValueError, TypeError)):
        portfolio_optimizer([value, 0.2])
    with pytest.raises((ValueError, TypeError)):
        inverse_volatility_weights([value, 0.2])


@pytest.mark.parametrize(
    "changes",
    [
        {"assets": []},
        {"assets": ASSETS * (MAX_ASSETS + 1)},
        {"assets": [None]},
        {"assets": [ASSETS[0], ASSETS[0]]},
        {"assets": [{"symbol": "../A", "volatility": 0.2}]},
        {"assets": [{"symbol": "A ", "volatility": 0.2}]},
        {"assets": [{"symbol": "A", "volatility": 0.2, "authorized": True}]},
        {"assets": [{"symbol": "A", "volatility": 0}]},
        {"max_weight": 0},
        {"max_weight": 1.1},
        {"cash_reserve": -0.1},
        {"cash_reserve": 1.1},
        {"max_scenario_loss": 0.1},
        {"scenarios": {}},
        {"scenarios": [None]},
        {"scenarios": [{"id": "test", "shocks": {"A": -0.1}}]},
        {"scenarios": [{"id": "test", "shocks": {"A": -0.1, "B": -0.2, "C": 0}}]},
        {"scenarios": [{"id": "test", "shocks": {"A": -1.1, "B": 0}}]},
        {"scenarios": [{"id": "test", "shocks": {"A": True, "B": 0}}]},
        {"scenarios": [{"id": "test", "shocks": {"A": 0, "B": 0}}] * 2},
        {
            "scenarios": [
                {"id": str(i), "shocks": {"A": 0, "B": 0}} for i in range(MAX_SCENARIOS + 1)
            ]
        },
    ],
)
def test_malformed_ambiguous_or_incomplete_constraints_are_rejected(changes):
    with pytest.raises((ValueError, TypeError)):
        allocation_preview(**{"assets": ASSETS, **changes})


def test_loss_smaller_than_float_range_does_not_silently_pass_a_zero_budget():
    report = allocation_preview(
        [
            {"symbol": "A", "volatility": 0.2, "max_weight": 5e-324},
        ],
        scenarios=[{"id": "tiny", "shocks": {"A": -5e-324}}],
        max_scenario_loss=0,
    )
    assert report["scenarios"][0]["loss"] == 0  # The float projection underflows.
    assert report["scenarios"][0]["loss_decimal"] == "2.5E-647"
    assert report["scenario_policy"] == "breach"


def test_legacy_optimizer_preserves_uniform_cap_and_infeasibility_contract():
    assert portfolio_optimizer([0.1, 0.2], 0.6)["weights"] == pytest.approx([0.6, 0.4])
    assert portfolio_optimizer([5e-324, 0.2], 0.6)["weights"] == pytest.approx([0.6, 0.4])
    assert inverse_volatility_weights((5e-324, 0.2)) == [1, 0]
    with pytest.raises(ValueError):
        portfolio_optimizer([0.1, 0.2], 0.4)
    with pytest.raises(ValueError):
        portfolio_optimizer([0.2] * (MAX_ASSETS + 1))


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_CONTROL_DB", str(tmp_path / "control.sqlite3"))
    monkeypatch.setenv(
        "OSIRIS_ADMIN_TOKENS",
        json.dumps(
            {
                "analyst": "allocation-analyst-fixture",
                "viewer": "allocation-viewer-fixture",
            }
        ),
    )
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "paper.sqlite3"))
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_preview_api_is_authorized_no_store_and_has_no_order_side_effects(api, tmp_path):
    payload = {"assets": ASSETS, "max_weight": 0.6, "cash_reserve": 0.1}
    response = api.post(
        "/control-plane/portfolio/preview",
        json=payload,
        headers={"Authorization": "Bearer allocation-analyst-fixture"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == allocation_preview(**payload)
    assert response.headers["cache-control"] == "no-store"
    assert not (tmp_path / "paper.sqlite3").exists()


def test_preview_denies_unauthenticated_viewers_and_unknown_fields(api, monkeypatch):
    path, payload = "/control-plane/portfolio/preview", {"assets": ASSETS}
    assert api.post(path, json=payload).status_code == 401
    assert (
        api.post(
            path, json=payload, headers={"Authorization": "Bearer allocation-viewer-fixture"}
        ).status_code
        == 403
    )
    headers = {"Authorization": "Bearer allocation-analyst-fixture"}
    assert (
        api.post(path, json={**payload, "live_execution": True}, headers=headers).status_code == 422
    )
    assert (
        api.post(
            path, json={"assets": [{"symbol": "A", "volatility": True}]}, headers=headers
        ).status_code
        == 422
    )
    monkeypatch.delenv("OSIRIS_CONTROL_DB")
    assert api.post(path, json=payload, headers=headers).status_code == 503


def test_preview_withholds_secret_shaped_asset_names(api):
    response = api.post(
        "/control-plane/portfolio/preview",
        json={
            "assets": [{"symbol": "allocation-analyst-fixture", "volatility": 0.2}],
        },
        headers={"Authorization": "Bearer allocation-analyst-fixture"},
    )
    assert response.status_code == 500
    assert "allocation-analyst-fixture" not in response.text


def test_legacy_api_returns_finite_json_for_the_previously_failing_input():
    from osiris_api.main import app

    response = TestClient(app).post(
        "/research/portfolio/optimize",
        json={
            "volatilities": [5e-324, 0.2],
            "max_weight": 0.6,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["weights"] == pytest.approx([0.6, 0.4])
    json.dumps(response.json(), allow_nan=False)
