"""Leakage-safe walk-forward research harness with costs and governance (#178).

Research diagnostics only. The harness enforces point-in-time visibility by
construction: at decision index i the model sees only ticks whose
available_time precedes the decision, and exposure decided at i-1 is applied
to the return ending at i. No in-sample metrics are reported, no live
execution is simulated, and no return expectation is established.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from math import isfinite, log, sqrt
from statistics import mean, pstdev
from typing import Any, Callable, Mapping, Protocol, Sequence

from .authorized_feeds import ticks_digest, validate_feed_tick

REPORT_SCHEMA = "osiris.walk_forward_report.v1"
GOVERNANCE_SCHEMA = "osiris.model_governance.v1"
MAX_TICKS = 10_000
MAX_FOLDS = 64
DRIFT_PSI_FLAG = 0.25
_DRIFT_BINS = 10
_DRIFT_EPS = 1e-6


class WalkForwardRejection(ValueError):
    """Raised when walk-forward inputs or invariants are violated."""


class ResearchModel(Protocol):
    """Model contract: fit on visible ticks, then expose in [-1, 1]."""

    def fit(self, visible_ticks: Sequence[Mapping[str, Any]]) -> None: ...

    def exposure(self, visible_ticks: Sequence[Mapping[str, Any]]) -> float: ...


def _bounded_exposure(value: Any) -> float:
    try:
        exposure = float(value)
    except (TypeError, ValueError) as exc:
        raise WalkForwardRejection("model_exposure_not_numeric") from exc
    if not isfinite(exposure) or not -1.0 <= exposure <= 1.0:
        raise WalkForwardRejection("model_exposure_out_of_bounds")
    return exposure


def _price_series(ticks: Sequence[Mapping[str, Any]]) -> list[float]:
    prices = [float(t["price"]) for t in ticks]
    if not all(isfinite(p) and p > 0 for p in prices):
        raise WalkForwardRejection("tick_prices_invalid")
    return prices


def _prepare_ticks(
    ticks: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    now: datetime,
) -> list[dict[str, Any]]:
    if isinstance(ticks, (str, bytes)) or not isinstance(ticks, Sequence):
        raise WalkForwardRejection("ticks_must_be_a_sequence")
    if not 2 <= len(ticks) <= MAX_TICKS:
        raise WalkForwardRejection("ticks_count_out_of_bounds")
    normalized = []
    for tick in ticks:
        clean = validate_feed_tick(tick, now=now)
        if clean["symbol"] != symbol:
            raise WalkForwardRejection("tick_symbol_mismatch")
        normalized.append(clean)
    # Point-in-time order: what was available first is seen first.
    normalized.sort(key=lambda t: (t["available_time"], t["event_time"]))
    return normalized


def _max_drawdown(returns: Sequence[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        worst = max(worst, (peak - equity) / peak)
    return worst


def _psi(train: Sequence[float], test: Sequence[float]) -> float:
    """Population stability index between two return samples (10 bins)."""
    low, high = min(train), max(train)
    if high == low:
        return 0.0 if set(test) <= set(train) else 1.0
    width = (high - low) / _DRIFT_BINS
    psi = 0.0
    for bucket in range(_DRIFT_BINS):
        left = low + bucket * width
        right = high if bucket == _DRIFT_BINS - 1 else left + width
        a = sum(1 for v in train if left <= v <= right) / len(train)
        b = sum(1 for v in test if left <= v <= right) / len(test)
        a = max(a, _DRIFT_EPS)
        b = max(b, _DRIFT_EPS)
        psi += (a - b) * log(a / b)
    return psi


def run_walk_forward(
    ticks: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    model_factory: Callable[[], ResearchModel],
    now: datetime,
    train_min: int = 8,
    test_size: int = 4,
    step: int | None = None,
    cost_bps_per_unit_turnover: float = 0.0,
) -> dict[str, Any]:
    """Anchored walk-forward over point-in-time ticks.

    Fold f: train on the first train_min + f*step visible points, then score
    the next test_size decisions out of sample. Exposure at decision i-1 is
    applied to the return ending at i; turnover is charged cost bps.
    """
    if not isinstance(train_min, int) or not 4 <= train_min <= MAX_TICKS - 2:
        raise WalkForwardRejection("train_min_out_of_bounds")
    if not isinstance(test_size, int) or not 2 <= test_size <= MAX_TICKS // 2:
        raise WalkForwardRejection("test_size_out_of_bounds")
    step = step if step is not None else test_size
    if not isinstance(step, int) or not 1 <= step <= MAX_TICKS // 2:
        raise WalkForwardRejection("step_out_of_bounds")
    if not isfinite(cost_bps_per_unit_turnover) or not 0 <= cost_bps_per_unit_turnover <= 10_000:
        raise WalkForwardRejection("cost_bps_out_of_bounds")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise WalkForwardRejection("now_missing_timezone")
    now = now.astimezone(timezone.utc)
    prepared = _prepare_ticks(ticks, symbol=symbol, now=now)
    prices = _price_series(prepared)
    returns = [prices[i] / prices[i - 1] - 1.0 for i in range(1, len(prices))]
    if any(r <= -1.0 for r in returns):
        raise WalkForwardRejection("return_below_minus_100_percent")
    # Decision points align with returns: decision i uses ticks[0..i], its
    # exposure applies to returns[i] (price move i -> i+1).
    n_decisions = len(returns)
    if n_decisions < train_min + test_size:
        raise WalkForwardRejection("insufficient_points_for_one_fold")

    folds: list[dict[str, Any]] = []
    fold_index = 0
    train_end = train_min
    while train_end + test_size <= n_decisions and fold_index < MAX_FOLDS:
        test_end = train_end + test_size
        train_ticks = prepared[: train_end + 1]  # visible up to first test decision
        # Defense-in-depth: no training tick may be available after the first
        # test tick became available.
        if train_ticks[-1]["available_time"] > prepared[train_end + 1]["available_time"]:
            raise WalkForwardRejection("point_in_time_invariant_violated")
        model = model_factory()
        model.fit(train_ticks)
        fold_returns: list[float] = []
        fold_costs = 0.0
        fold_turnover = 0.0
        previous_exposure = 0.0
        hits = 0
        for decision in range(train_end, test_end):
            visible = prepared[: decision + 1]
            exposure = _bounded_exposure(model.exposure(visible))
            turnover = abs(exposure - previous_exposure)
            cost = turnover * cost_bps_per_unit_turnover / 10_000.0
            realized = exposure * returns[decision] - cost
            fold_returns.append(realized)
            fold_turnover += turnover
            fold_costs += cost
            previous_exposure = exposure
            if (exposure > 0 and returns[decision] > 0) or (exposure < 0 and returns[decision] < 0):
                hits += 1
        folds.append({
            "fold": fold_index,
            "train_points": len(train_ticks),
            "test_decisions": test_size,
            "oos_total_return_net": sum(fold_returns),
            "oos_mean_return_net": mean(fold_returns),
            "oos_volatility": pstdev(fold_returns) if len(fold_returns) > 1 else 0.0,
            "oos_sharpe_like": (
                mean(fold_returns) / pstdev(fold_returns) * sqrt(len(fold_returns))
                if len(fold_returns) > 1 and pstdev(fold_returns) > 0 else 0.0
            ),
            "oos_max_drawdown": _max_drawdown(fold_returns),
            "hit_rate": hits / test_size,
            "turnover": fold_turnover,
            "costs_paid": fold_costs,
            "cost_bps_per_unit_turnover": cost_bps_per_unit_turnover,
        })
        fold_index += 1
        train_end += step
    if not folds:
        raise WalkForwardRejection("no_folds_evaluated")

    train_returns = returns[:train_min]
    last_fold_returns = returns[-test_size:]
    drift_psi = _psi(train_returns, last_fold_returns)
    aggregate = {
        "folds": len(folds),
        "oos_total_return_net": sum(f["oos_total_return_net"] for f in folds),
        "oos_mean_fold_return_net": mean(f["oos_total_return_net"] for f in folds),
        "oos_mean_hit_rate": mean(f["hit_rate"] for f in folds),
        "oos_worst_fold_drawdown": max(f["oos_max_drawdown"] for f in folds),
        "total_turnover": sum(f["turnover"] for f in folds),
        "total_costs_paid": sum(f["costs_paid"] for f in folds),
    }
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "symbol": symbol,
        "points": len(prepared),
        "config": {
            "train_min": train_min,
            "test_size": test_size,
            "step": step,
            "cost_bps_per_unit_turnover": cost_bps_per_unit_turnover,
            "anchored_training": True,
        },
        "folds_detail": folds,
        "aggregate": aggregate,
        "drift": {
            "method": "population_stability_index_10_bin_train_vs_last_fold",
            "psi": drift_psi,
            "flagged": drift_psi > DRIFT_PSI_FLAG,
            "threshold": DRIFT_PSI_FLAG,
        },
        "data_sha256": ticks_digest(list(prepared)),
        "in_sample_metrics": "not_reported",
        "signal_alignment": "exposure_at_decision_i-1_applied_to_return_ending_at_i",
        "execution": "not_simulated",
        "live_trading_authorized": False,
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"))
    report["report_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return report


def governance_record(
    *,
    model_id: str,
    model_version: str,
    params: Mapping[str, Any],
    report: Mapping[str, Any],
) -> dict[str, Any]:
    """Model governance entry bound to a walk-forward report. Research-only:
    approval lists start empty and live trading is never authorized here."""
    for value, field in ((model_id, "model_id"), (model_version, "model_version")):
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 128:
            raise WalkForwardRejection(f"{field}_invalid")
    if report.get("schema") != REPORT_SCHEMA or not isinstance(report.get("report_sha256"), str):
        raise WalkForwardRejection("report_not_validated_walk_forward")
    try:
        json.dumps(params, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise WalkForwardRejection("params_not_json_serializable") from exc
    return {
        "schema": GOVERNANCE_SCHEMA,
        "model_id": model_id.strip(),
        "model_version": model_version.strip(),
        "status": "research_only",
        "approvals": [],
        "live_trading_authorized": False,
        "params": dict(params),
        "data_sha256": report["data_sha256"],
        "report_sha256": report["report_sha256"],
        "out_of_sample": report["aggregate"],
        "drift": report["drift"],
        "governance_boundary": "research_diagnostics_only_no_production_approval",
    }


class MomentumReferenceModel:
    """Small deterministic reference model for tests and sandbox studies."""

    def __init__(self, window: int = 4, scale: float = 0.5) -> None:
        if not 1 <= window <= 256 or not 0 < scale <= 1.0:
            raise WalkForwardRejection("reference_model_config_invalid")
        self._window = window
        self._scale = scale

    def fit(self, visible_ticks: Sequence[Mapping[str, Any]]) -> None:
        if len(visible_ticks) < 2:
            raise WalkForwardRejection("reference_model_needs_two_points")

    def exposure(self, visible_ticks: Sequence[Mapping[str, Any]]) -> float:
        prices = [float(t["price"]) for t in visible_ticks][-self._window - 1:]
        if len(prices) < 2:
            return 0.0
        total = prices[-1] / prices[0] - 1.0
        return max(-1.0, min(1.0, self._scale if total > 0 else (-self._scale if total < 0 else 0.0)))
