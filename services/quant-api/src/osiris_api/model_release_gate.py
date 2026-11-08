"""Offline out-of-sample model release gate; not evidence of profitability."""
import math


def evaluate_model(*, train_end, test_start, observations, leakage_checks_passed,
                   estimated_cost_bps, gross_edge_bps, model_approved):
    reasons = []
    if not isinstance(train_end, int) or not isinstance(test_start, int) or train_end >= test_start:
        reasons.append("invalid_temporal_split")
    if not isinstance(observations, int) or isinstance(observations, bool) or observations < 100:
        reasons.append("insufficient_oos_samples")
    if leakage_checks_passed is not True:
        reasons.append("leakage_checks_missing")
    if model_approved is not True:
        reasons.append("model_not_approved")
    for value in (estimated_cost_bps, gross_edge_bps):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            reasons.append("invalid_cost_or_edge")
            break
    else:
        if estimated_cost_bps < 0 or gross_edge_bps <= estimated_cost_bps:
            reasons.append("no_positive_net_edge")
    return {"paper_research_eligible": not reasons, "live_execution_enabled": False, "reasons": reasons}
