"""Purged fitted-ridge walk-forward cost diagnostics for a verified quote archive.

This derived analysis is deliberately post-capture, not an independent blinded
validation. Original keyless archive contents and reproduction are unchanged.
"""
from __future__ import annotations

import hashlib
import math
import platform
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from .control_plane.database import Database, digest
from .control_plane.models import ModelService
from .keyless_research import _time, response_payload, verify_archive

SCHEMA = "osiris.ridge-oos-cost-diagnostic.v1"
PLAN_SCHEMA = "osiris.ridge-oos-cost-plan.v1"
POLICY = {
    "feature_lags": 3, "train_min": 8, "test_size": 4, "step": 4, "ridge": 0.001,
    "position_rule": "long_one_if_prediction_positive_else_flat",
    "fold_boundaries": "independent_flat_start_and_terminal_liquidation",
    "fixed_cost_bps_per_unit_turnover": [0, 10, 25],
    "observed_spread": "half_quoted_spread_per_unit_turnover",
}
SOURCE_FILES = ("ridge_oos_costs.py", "control_plane/models.py", "control_plane/database.py")


def source_manifest() -> dict:
    root = Path(__file__).resolve().parent
    files = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in SOURCE_FILES}
    script = root.parents[3] / "scripts/record_ridge_oos_costs.py"
    files["scripts/record_ridge_oos_costs.py"] = hashlib.sha256(script.read_bytes()).hexdigest()
    return files


def runtime() -> dict:
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "numpy": np.__version__}


def build_plan(archive: dict, *, archive_commit: str, archive_sha256: str,
               analysis_commit: str, created_at: datetime) -> dict:
    if not isinstance(analysis_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", analysis_commit):
        raise ValueError("invalid_analysis_commit")
    if not isinstance(created_at, datetime) or created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("analysis_time_must_be_aware")
    verify_archive(archive, expected_commit=archive_commit, expected_sha256=archive_sha256)
    if created_at.astimezone(UTC) < _time(archive["ticks"][-1]["available_time"]):
        raise ValueError("derived_analysis_cannot_precede_capture")
    return {
        "schema": PLAN_SCHEMA, "analysis_commit": analysis_commit,
        "archive_commit": archive_commit, "archive_sha256": archive_sha256,
        "archive_data_sha256": archive["data_sha256"], "created_at": created_at.isoformat(),
        "policy": {**POLICY, "fixed_cost_bps_per_unit_turnover": list(POLICY["fixed_cost_bps_per_unit_turnover"])},
        "analysis_source_sha256": source_manifest(), "runtime": runtime(),
        "purpose": "post_capture_paper_research_diagnostic", "blinded_holdout": False,
        "independent_model_reviewed": False, "vendor_rights_verified": False,
        "release_gate_accepted": False, "live_trading_authorized": False,
    }


def _validate_plan(archive: dict, plan: dict, expected_sha256: str) -> None:
    if not isinstance(plan, dict) or digest(plan) != expected_sha256:
        raise ValueError("ridge_analysis_plan_digest_mismatch")
    try:
        rebuilt = build_plan(archive, archive_commit=plan["archive_commit"],
            archive_sha256=plan["archive_sha256"], analysis_commit=plan["analysis_commit"],
            created_at=_time(plan["created_at"]))
    except (KeyError, TypeError):
        raise ValueError("invalid_ridge_analysis_plan") from None
    if plan != rebuilt:
        raise ValueError("ridge_analysis_plan_or_source_changed")


def _availability_ms(value: str) -> int:
    stamp = _time(value)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = stamp - epoch
    # Millisecond model timestamps round availability up, never before receipt.
    return delta.days * 86_400_000 + delta.seconds * 1000 + (delta.microseconds + 999) // 1000


def _rows(archive: dict) -> list[dict]:
    ticks, captures, upstream_plan = archive["ticks"], archive["captures"], archive["plan"]
    prices = [float(tick["price"]) for tick in ticks]
    spreads = []
    for capture in captures[1:]:
        value = response_payload(capture, upstream_plan, "ticker")
        bid, ask = float(value["bid"]), float(value["ask"])
        spread = (ask - bid) / ((ask + bid) / 2) * 10_000
        if not math.isfinite(spread) or spread < 0:
            raise ValueError("invalid_observed_spread")
        spreads.append(spread)
    rows = []
    for index in range(3, len(ticks) - 1):
        decision_ms = _availability_ms(ticks[index]["available_time"])
        label_ms = _availability_ms(ticks[index + 1]["available_time"])
        if rows and decision_ms <= rows[-1]["event_ms"] or label_ms <= decision_ms:
            raise ValueError("ridge_feature_or_label_time_not_strict")
        rows.append({
            "event_ms": decision_ms, "available_ms": decision_ms, "label_available_ms": label_ms,
            "features": [prices[index - lag] / prices[index - lag - 1] - 1 for lag in range(3)],
            "target": prices[index + 1] / prices[index] - 1,
            "tick_index": index, "entry_spread_bps": spreads[index],
            "exit_spread_bps": spreads[index + 1],
        })
    return rows


def cost_diagnostic(decisions: list[dict], cost_bps: int, *, observed_spread: bool) -> dict:
    """Unlevered long/flat diagnostic, resetting flat at each fold boundary.

    Costs are additive return sensitivities per unit turnover, not a fill model.
    Entry/rebalance uses the decision quote; terminal liquidation uses the last
    label quote. The final liquidation cost is charged to the final interval.
    """
    if type(cost_bps) is not int or cost_bps not in {0, 10, 25} or type(observed_spread) is not bool:
        raise ValueError("invalid_ridge_cost_policy")
    if not isinstance(decisions, list) or not decisions:
        raise ValueError("ridge_cost_decisions_required")
    previous, gross_equity, net_equity, turnover, fixed_cost, spread_cost = 0, 1.0, 1.0, 0.0, 0.0, 0.0
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            raise ValueError("invalid_ridge_cost_decision")
        prediction, actual = decision.get("prediction"), decision.get("target")
        entry, exit_spread = decision.get("entry_spread_bps"), decision.get("exit_spread_bps")
        if any(type(value) not in (int, float) or not math.isfinite(value)
               for value in (prediction, actual, entry, exit_spread)) or entry < 0 or exit_spread < 0:
            raise ValueError("invalid_ridge_cost_decision")
        position = int(prediction > 0)
        rebalance = abs(position - previous)
        liquidation = position if index == len(decisions) - 1 else 0
        units = rebalance + liquidation
        fixed = units * cost_bps / 10_000
        quoted = (rebalance * entry + liquidation * exit_spread) / 20_000 if observed_spread else 0
        gross_factor = 1 + position * actual
        net_factor = gross_factor - fixed - quoted
        if not 0 < net_factor <= gross_factor or not math.isfinite(net_factor):
            raise ValueError("unusable_ridge_cost_return")
        gross_equity *= gross_factor
        net_equity *= net_factor
        turnover += units
        fixed_cost += fixed
        spread_cost += quoted
        previous = position
    if not all(math.isfinite(value) for value in (gross_equity, net_equity, fixed_cost, spread_cost)):
        raise ValueError("nonfinite_ridge_cost_result")
    return {"intervals": len(decisions), "gross_return": gross_equity - 1,
            "net_return": net_equity - 1, "unit_turnover": turnover,
            "fixed_cost_sum": fixed_cost, "quoted_spread_cost_sum": spread_cost,
            "observed_quoted_spread_applied": observed_spread, "venue_calibrated": False}


def evaluate_archive(archive: dict, plan: dict, *, expected_plan_sha256: str) -> dict:
    _validate_plan(archive, plan, expected_plan_sha256)
    rows = _rows(archive)
    folds = []
    policy = plan["policy"]
    with tempfile.TemporaryDirectory(prefix="osiris-ridge-oos-") as folder:
        service = ModelService(Database(str(Path(folder) / "models.sqlite3")))
        for start in range(policy["train_min"] + 1, len(rows) - policy["test_size"] + 1, policy["step"]):
            test = rows[start:start + policy["test_size"]]
            cutoff = test[0]["event_ms"]
            train = [row for row in rows[:start] if row["label_available_ms"] < cutoff]
            if len(train) < policy["train_min"]:
                raise ValueError("insufficient_purged_ridge_training")
            training_fields = {"event_ms", "available_ms", "label_available_ms", "features", "target"}
            fitted_rows = [{key: row[key] for key in training_fields} for row in train + test]
            model_id = "ridge-cost-fold-" + str(len(folds))
            model = service.train(model_id, fitted_rows, holdout=len(test), ridge=policy["ridge"],
                feature_names=["last_return", "previous_return", "third_return"])
            decisions = [{**row, "prediction": service.predict(model_id, row["features"])} for row in test]
            costs = {str(cost): {
                "fixed_only": cost_diagnostic(decisions, cost, observed_spread=False),
                "fixed_plus_quote_spread": cost_diagnostic(decisions, cost, observed_spread=True),
            } for cost in policy["fixed_cost_bps_per_unit_turnover"]}
            folds.append({"test_start_row": start, "train_rows": len(train),
                "purged_rows": start - len(train), "training_rows_sha256": digest(fitted_rows[:-len(test)]),
                "holdout_start_ms": cutoff, "model": model, "decisions": decisions, "costs": costs})
    if not folds:
        raise ValueError("insufficient_ridge_walk_forward_folds")
    aggregate = {}
    for cost in policy["fixed_cost_bps_per_unit_turnover"]:
        aggregate[str(cost)] = {}
        for basis in ("fixed_only", "fixed_plus_quote_spread"):
            equity = math.prod(1 + fold["costs"][str(cost)][basis]["net_return"] for fold in folds)
            aggregate[str(cost)][basis] = {
                "net_return": equity - 1,
                "unit_turnover": sum(fold["costs"][str(cost)][basis]["unit_turnover"] for fold in folds),
                "fixed_cost_sum": sum(fold["costs"][str(cost)][basis]["fixed_cost_sum"] for fold in folds),
                "quoted_spread_cost_sum": sum(fold["costs"][str(cost)][basis]["quoted_spread_cost_sum"] for fold in folds),
            }
    result = {"schema": SCHEMA, "plan": plan, "plan_sha256": expected_plan_sha256,
              "folds": folds, "aggregate": aggregate, "source_samples": len(archive["ticks"]),
              "source_capture_seconds": (_time(archive["ticks"][-1]["available_time"])
                  - _time(archive["ticks"][0]["available_time"])).total_seconds(),
              "post_capture_diagnostic": True, "blinded_holdout": False,
              "independent_model_reviewed": False, "vendor_rights_verified": False,
              "release_gate_accepted": False, "live_trading_authorized": False,
              "limitations": ["Short quote sample; representative independent strategy validation is pending.",
                  "Positions use next observed trade returns; no executable fill/queue/impact/margin model.",
                  "Quoted spreads and fixed costs are sensitivity assumptions, not venue calibration.",
                  "Fold boundaries deliberately liquidate; this is not a continuous deployed strategy.",
                  "This analysis was planned after capture and is not a blinded holdout."]}
    result["report_sha256"] = digest(result)
    return result


def verify_report(archive: dict, report: dict, *, expected_sha256: str, analysis_commit: str) -> dict:
    if not isinstance(report, dict) or report.get("report_sha256") != expected_sha256:
        raise ValueError("ridge_report_digest_mismatch")
    body = {k: v for k, v in report.items() if k != "report_sha256"}
    if digest(body) != expected_sha256:
        raise ValueError("ridge_report_digest_mismatch")
    if not isinstance(report.get("plan"), dict):
        raise ValueError("invalid_ridge_analysis_plan")
    if report["plan"].get("analysis_commit") != analysis_commit:
        raise ValueError("ridge_analysis_commit_mismatch")
    rebuilt = evaluate_archive(archive, report.get("plan"), expected_plan_sha256=report.get("plan_sha256"))
    if report != rebuilt:
        raise ValueError("ridge_report_reproduction_mismatch")
    return {"consistent": True, "reproduced": True, "folds": len(report["folds"]),
            "report_sha256": expected_sha256, "release_gate_accepted": False,
            "independent_model_reviewed": False, "live_trading_authorized": False}
