"""Fail-closed out-of-sample eligibility checks. Refs #178 and #207.

Eligibility is paper-research only. These tests are not a model-risk signoff.
"""
import pytest

from osiris_api.model_release_gate import evaluate_model


GOOD = dict(
    train_end=100,
    test_start=101,
    observations=150,
    leakage_checks_passed=True,
    estimated_cost_bps=2,
    gross_edge_bps=3,
    model_approved=True,
)


def test_paper_research_eligible_but_never_live():
    result = evaluate_model(**GOOD)
    assert result == {
        "paper_research_eligible": True,
        "live_execution_enabled": False,
        "reasons": [],
    }


def test_minimum_sample_boundary_is_eligible_without_live_execution():
    result = evaluate_model(**{**GOOD, "observations": 100})
    assert result["paper_research_eligible"] is True
    assert result["live_execution_enabled"] is False


def test_leakage_and_costs_fail_closed():
    result = evaluate_model(**{**GOOD, "leakage_checks_passed": False, "estimated_cost_bps": 4})
    assert result["paper_research_eligible"] is False
    assert result["live_execution_enabled"] is False
    assert result["reasons"] == ["leakage_checks_missing", "no_positive_net_edge"]


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"train_end": 101, "test_start": 101}, "invalid_temporal_split"),
        ({"train_end": 200, "test_start": 101}, "invalid_temporal_split"),
        ({"train_end": True, "test_start": 101}, "invalid_temporal_split"),
        ({"train_end": 100, "test_start": True}, "invalid_temporal_split"),
        ({"train_end": 100.0, "test_start": 101}, "invalid_temporal_split"),
        ({"train_end": "100", "test_start": "101"}, "invalid_temporal_split"),
        ({"observations": 99}, "insufficient_oos_samples"),
        ({"observations": 0}, "insufficient_oos_samples"),
        ({"observations": -5}, "insufficient_oos_samples"),
        ({"observations": True}, "insufficient_oos_samples"),
        ({"observations": 150.0}, "insufficient_oos_samples"),
        ({"leakage_checks_passed": False}, "leakage_checks_missing"),
        ({"leakage_checks_passed": 1}, "leakage_checks_missing"),
        ({"leakage_checks_passed": "true"}, "leakage_checks_missing"),
        ({"model_approved": False}, "model_not_approved"),
        ({"model_approved": 1}, "model_not_approved"),
        ({"model_approved": "yes"}, "model_not_approved"),
        ({"estimated_cost_bps": float("nan")}, "invalid_cost_or_edge"),
        ({"gross_edge_bps": float("inf")}, "invalid_cost_or_edge"),
        ({"estimated_cost_bps": True}, "invalid_cost_or_edge"),
        ({"gross_edge_bps": "3"}, "invalid_cost_or_edge"),
        ({"estimated_cost_bps": -1, "gross_edge_bps": 10}, "no_positive_net_edge"),
        ({"gross_edge_bps": 2}, "no_positive_net_edge"),
        ({"gross_edge_bps": 1}, "no_positive_net_edge"),
    ],
)
def test_simulated_eligibility_failures_stay_offline(overrides, reason):
    result = evaluate_model(**{**GOOD, **overrides})
    assert result["paper_research_eligible"] is False
    assert result["live_execution_enabled"] is False
    assert reason in result["reasons"]


def test_stacked_failures_are_all_reported():
    result = evaluate_model(
        train_end=5,
        test_start=5,
        observations=1,
        leakage_checks_passed=None,
        estimated_cost_bps=float("nan"),
        gross_edge_bps=float("-inf"),
        model_approved=False,
    )
    assert result["paper_research_eligible"] is False
    assert result["live_execution_enabled"] is False
    assert result["reasons"] == [
        "invalid_temporal_split",
        "insufficient_oos_samples",
        "leakage_checks_missing",
        "model_not_approved",
        "invalid_cost_or_edge",
    ]


def test_live_execution_cannot_be_requested():
    with pytest.raises(TypeError):
        evaluate_model(**GOOD, live_execution_enabled=True)
