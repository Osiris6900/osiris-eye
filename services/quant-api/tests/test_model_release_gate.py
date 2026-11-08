from osiris_api.model_release_gate import evaluate_model

GOOD = dict(train_end=100, test_start=101, observations=150, leakage_checks_passed=True, estimated_cost_bps=2, gross_edge_bps=3, model_approved=True)

def test_paper_research_eligible_but_never_live():
    result = evaluate_model(**GOOD)
    assert result["paper_research_eligible"] and result["live_execution_enabled"] is False

def test_leakage_and_costs_fail_closed():
    result = evaluate_model(**{**GOOD, "leakage_checks_passed": False, "estimated_cost_bps": 4})
    assert not result["paper_research_eligible"]
    assert "leakage_checks_missing" in result["reasons"]
