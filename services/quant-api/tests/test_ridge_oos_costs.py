"""Fitted-ridge cost diagnostics retain purging, lineage and honest scope."""
from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import patch

import numpy as np
import pytest

from osiris_api.control_plane.database import digest
from osiris_api.ridge_oos_costs import build_plan, cost_diagnostic, evaluate_archive, verify_report
from test_keyless_research import archive as captured_fixture

COMMIT = "b" * 40
CREATED = datetime(2026, 11, 14, tzinfo=UTC)


def configured():
    archive = captured_fixture()
    plan = build_plan(archive, archive_commit="a" * 40, archive_sha256=archive["archive_sha256"],
                      analysis_commit=COMMIT, created_at=CREATED)
    return archive, plan


def test_fitted_predictions_are_purged_and_scaling_uses_training_only():
    archive, plan = configured()
    report = evaluate_archive(archive, plan, expected_plan_sha256=digest(plan))
    assert len(report["folds"]) == 4
    for fold in report["folds"]:
        model = fold["model"]
        assert fold["purged_rows"] == 1
        assert fold["holdout_start_ms"] == model["holdout_start_ms"]
        assert model["train_rows"] == fold["train_rows"]
        assert model["holdout_rows"] == 4
        for decision in fold["decisions"]:
            assert decision["available_ms"] == decision["event_ms"]
            assert decision["label_available_ms"] > decision["event_ms"]
            expected = model["coefficients"][0] + sum(
                coefficient * (value - mean) / scale for coefficient, value, mean, scale in zip(
                    model["coefficients"][1:], decision["features"], model["mean"], model["scale"]))
            assert decision["prediction"] == expected
    first = report["folds"][0]
    prices = [float(tick["price"]) for tick in archive["ticks"]]
    training = [[prices[i-lag] / prices[i-lag-1] - 1 for lag in range(3)] for i in range(3, 11)]
    assert np.allclose(first["model"]["mean"], np.mean(training, axis=0), rtol=0, atol=1e-16)
    assert report["blinded_holdout"] is report["independent_model_reviewed"] is False
    assert report["release_gate_accepted"] is report["live_trading_authorized"] is False


def test_future_holdout_values_cannot_change_first_fitted_model():
    archive, plan = configured()
    original = evaluate_archive(archive, plan, expected_plan_sha256=digest(plan))
    # Change held-out prices and rebuild the original capture under its existing
    # valid hash/source contract. The first fit's inputs remain strictly earlier.
    import hashlib
    import json
    from osiris_api.keyless_research import build_archive
    changed = deepcopy(archive["captures"])
    for capture in changed[13:]:
        body = json.loads(capture["body_utf8"])
        body["price"] = str(float(body["price"]) * 1.02)
        capture["body_utf8"] = json.dumps(body)
        capture["body_sha256"] = hashlib.sha256(capture["body_utf8"].encode()).hexdigest()
    altered = build_archive(archive["plan"], changed, frozen_plan_sha256=archive["plan_sha256"])
    altered_plan = build_plan(altered, archive_commit="a" * 40, archive_sha256=altered["archive_sha256"],
                              analysis_commit=COMMIT, created_at=CREATED)
    result = evaluate_archive(altered, altered_plan, expected_plan_sha256=digest(altered_plan))
    a, b = original["folds"][0]["model"], result["folds"][0]["model"]
    for field in ("mean", "scale", "coefficients", "train_rows"):
        assert a[field] == b[field]
    assert a["holdout_mse"] != b["holdout_mse"]


def test_long_flat_turnover_and_terminal_exit_have_explicit_costs():
    decisions = [{"prediction": p, "target": r, "entry_spread_bps": 0, "exit_spread_bps": 0}
                 for p, r in [(1, .1), (1, -.05), (-1, .02), (1, .03)]]
    free = cost_diagnostic(decisions, 0, observed_spread=False)
    paid = cost_diagnostic(decisions, 10, observed_spread=False)
    assert free["unit_turnover"] == paid["unit_turnover"] == 4
    assert free["gross_return"] == pytest.approx(1.1 * .95 * 1.03 - 1)
    assert paid["net_return"] == pytest.approx(1.099 * .95 * .999 * 1.028 - 1)
    assert paid["fixed_cost_sum"] == pytest.approx(.004)
    for decision in decisions:
        decision.update(entry_spread_bps=20, exit_spread_bps=40)
    quoted = cost_diagnostic(decisions, 0, observed_spread=True)
    assert quoted["quoted_spread_cost_sum"] == pytest.approx(.005)
    assert quoted["net_return"] < free["net_return"]
    assert quoted["venue_calibrated"] is False


def test_reproduction_rejects_rehashed_approval_or_report_edits():
    archive, plan = configured()
    report = evaluate_archive(archive, plan, expected_plan_sha256=digest(plan))
    with patch("osiris_api.keyless_research._get", side_effect=AssertionError("offline")):
        verified = verify_report(archive, report, expected_sha256=report["report_sha256"], analysis_commit=COMMIT)
    assert verified["reproduced"] is True
    for change in (
        lambda value: value.update(independent_model_reviewed=True),
        lambda value: value["aggregate"]["10"]["fixed_only"].update(net_return=1e6),
        lambda value: value["folds"][0]["model"].update(coefficients=[1, 2, 3, 4]),
    ):
        edited = deepcopy(report)
        change(edited)
        edited["report_sha256"] = digest({k: v for k, v in edited.items() if k != "report_sha256"})
        with pytest.raises(ValueError, match="reproduction_mismatch"):
            verify_report(archive, edited, expected_sha256=edited["report_sha256"], analysis_commit=COMMIT)


def test_plan_digest_source_policy_and_capture_time_are_frozen():
    archive, plan = configured()
    edited = deepcopy(plan)
    edited["policy"]["ridge"] = 1
    with pytest.raises(ValueError, match="digest_mismatch"):
        evaluate_archive(archive, edited, expected_plan_sha256=digest(plan))
    with pytest.raises(ValueError, match="plan_or_source_changed"):
        evaluate_archive(archive, edited, expected_plan_sha256=digest(edited))
    with pytest.raises(ValueError, match="cannot_precede_capture"):
        build_plan(archive, archive_commit="a" * 40, archive_sha256=archive["archive_sha256"],
                   analysis_commit=COMMIT, created_at=datetime(2026, 1, 1, tzinfo=UTC))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "bad"])
def test_nonfinite_or_non_numeric_cost_inputs_refuse(bad):
    with pytest.raises(ValueError, match="invalid_ridge_cost_decision"):
        cost_diagnostic([{"prediction": bad, "target": .01, "entry_spread_bps": 1,
                          "exit_spread_bps": 1}], 10, observed_spread=True)


@pytest.mark.parametrize("bad", [None, [], {}, {"prediction": 1}])
def test_malformed_cost_decisions_fail_with_validation_error(bad):
    with pytest.raises(ValueError, match="invalid_ridge_cost_decision"):
        cost_diagnostic([bad], 10, observed_spread=True)


@pytest.mark.parametrize("bad", [None, [], "wrong"])
def test_malformed_rehashed_report_plan_is_a_validation_error(bad):
    archive, _ = configured()
    report = {"plan": bad}
    report["report_sha256"] = digest(report)
    with pytest.raises(ValueError, match="invalid_ridge_analysis_plan"):
        verify_report(archive, report, expected_sha256=report["report_sha256"], analysis_commit=COMMIT)


def test_millisecond_availability_never_precedes_receipt():
    from osiris_api.ridge_oos_costs import _availability_ms
    assert _availability_ms("1970-01-01T00:00:00.000001+00:00") == 1
    assert _availability_ms("1970-01-01T00:00:00.001000+00:00") == 1


def test_cli_evidence_is_exclusive_and_cannot_write_into_source(tmp_path):
    import importlib.util
    from pathlib import Path
    script = Path(__file__).resolve().parents[3] / "scripts/record_ridge_oos_costs.py"
    spec = importlib.util.spec_from_file_location("ridge_cli", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = tmp_path / "existing.json"
    target.write_text("preserved")
    with pytest.raises(FileExistsError):
        module._write(target, {"changed": True})
    assert target.read_text() == "preserved"
    with pytest.raises(ValueError, match="outside_checkout"):
        module._write(module.ROOT / "ridge-evidence-must-not-be-created.json", {})
    assert not (module.ROOT / "ridge-evidence-must-not-be-created.json").exists()
