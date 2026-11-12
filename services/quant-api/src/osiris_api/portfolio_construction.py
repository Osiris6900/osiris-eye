"""Bounded long-only allocation previews using caller-supplied risk inputs.

Inverse volatility is a baseline, not covariance optimization. Stress limits
apply only to the submitted scenarios and never authorize an order.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from decimal import Context, Decimal, localcontext

MAX_ASSETS = 128
MAX_SCENARIOS = 32
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}")


def finite_number(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name}_must_be_a_finite_number")
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise ValueError(f"{name}_must_be_a_finite_number") from None
    if not math.isfinite(number):
        raise ValueError(f"{name}_must_be_a_finite_number")
    return number


def capped_inverse_volatility_weights(
    volatilities: Sequence[float],
    caps: Sequence[float],
    budget: float = 1.0,
) -> list[float]:
    if (
        not isinstance(volatilities, (list, tuple))
        or not isinstance(caps, (list, tuple))
        or not 1 <= len(volatilities) <= MAX_ASSETS
        or len(caps) != len(volatilities)
    ):
        raise ValueError("bounded_aligned_volatilities_and_caps_required")
    vols = [finite_number(value, "volatility") for value in volatilities]
    limits = [finite_number(value, "asset_cap") for value in caps]
    budget = finite_number(budget, "investment_budget")
    if (
        any(value <= 0 for value in vols)
        or any(not 0 <= value <= 1 for value in limits)
        or not 0 <= budget <= 1
    ):
        raise ValueError("positive_volatilities_and_unit_interval_caps_and_budget_required")
    target = min(budget, math.fsum(limits))
    weights = [0.0] * len(vols)
    active = [index for index, cap in enumerate(limits) if cap > 0]
    remaining = target
    while active and remaining > 0:
        # Re-scale on every cap round. Direct 1/vol overflows for subnormal
        # inputs; reusing the first scale can underflow after that asset caps.
        scale = min(vols[index] for index in active)
        scores = {index: scale / vols[index] for index in active}
        total = math.fsum(scores.values())  # At least one score is exactly 1.
        proposals = {index: remaining * (scores[index] / total) for index in active}
        saturated = [index for index in active if proposals[index] >= limits[index]]
        if not saturated:
            for index in active:
                weights[index] = proposals[index]
            break
        for index in saturated:
            weights[index] = limits[index]
        saturated_set = set(saturated)
        active = [index for index in active if index not in saturated_set]
        remaining = max(0.0, target - math.fsum(weights))
    # Keep roundoff on the cash side, rather than funding above the budget.
    if math.fsum(weights) > target:
        index = max(range(len(weights)), key=weights.__getitem__)
        excess = math.fsum(weights) - target
        weights[index] = max(0.0, math.nextafter(weights[index] - excess, 0.0))
    if any(
        not math.isfinite(value) or not 0 <= value <= cap for value, cap in zip(weights, limits)
    ) or not math.isclose(math.fsum(weights), target, rel_tol=0, abs_tol=1e-12):
        raise ValueError("allocation_numeric_invariant_failed")
    return weights


def _name(value, field: str) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError(f"invalid_{field}")
    return value


def allocation_preview(
    assets: list[dict],
    *,
    max_weight: float = 1.0,
    cash_reserve: float = 0.0,
    scenarios: list[dict] | None = None,
    max_scenario_loss: float | None = None,
) -> dict:
    if not isinstance(assets, list) or not 1 <= len(assets) <= MAX_ASSETS:
        raise ValueError("bounded_nonempty_assets_required")
    cap = finite_number(max_weight, "max_weight")
    reserve = finite_number(cash_reserve, "cash_reserve")
    if not 0 < cap <= 1 or not 0 <= reserve <= 1:
        raise ValueError("invalid_concentration_cap_or_cash_reserve")
    rows, symbols = [], set()
    for asset in assets:
        if (
            not isinstance(asset, dict)
            or not {"symbol", "volatility"} <= set(asset)
            or set(asset) - {"symbol", "volatility", "max_weight"}
        ):
            raise ValueError("invalid_asset_fields")
        symbol = _name(asset["symbol"], "asset_symbol")
        if symbol in symbols:
            raise ValueError("duplicate_asset_symbol")
        symbols.add(symbol)
        volatility = finite_number(asset["volatility"], "volatility")
        asset_cap = finite_number(asset.get("max_weight", cap), "asset_cap")
        if volatility <= 0 or not 0 <= asset_cap <= 1:
            raise ValueError("invalid_asset_volatility_or_cap")
        rows.append({"symbol": symbol, "volatility": volatility, "max_weight": min(cap, asset_cap)})
    if scenarios is None:
        scenarios = []
    if not isinstance(scenarios, list) or len(scenarios) > MAX_SCENARIOS:
        raise ValueError("bounded_scenarios_required")
    loss_limit = (
        None if max_scenario_loss is None else finite_number(max_scenario_loss, "max_scenario_loss")
    )
    if loss_limit is not None and (not 0 <= loss_limit <= 1 or not scenarios):
        raise ValueError("scenario_loss_limit_requires_scenarios_and_unit_interval")
    checked_scenarios, scenario_ids = [], set()
    for scenario in scenarios:
        if not isinstance(scenario, dict) or set(scenario) != {"id", "shocks"}:
            raise ValueError("invalid_scenario_fields")
        scenario_id = _name(scenario["id"], "scenario_id")
        if scenario_id in scenario_ids:
            raise ValueError("duplicate_scenario_id")
        scenario_ids.add(scenario_id)
        shocks = scenario["shocks"]
        if not isinstance(shocks, dict) or set(shocks) != symbols:
            raise ValueError("scenario_shocks_must_cover_exactly_the_assets")
        parsed = {
            symbol: finite_number(value, "scenario_shock") for symbol, value in shocks.items()
        }
        if any(value < -1 for value in parsed.values()):
            raise ValueError("long_only_asset_return_cannot_be_below_minus_one")
        checked_scenarios.append({"id": scenario_id, "shocks": parsed})
    budget = 1.0 - reserve
    if 1.0 - budget < reserve:
        budget = math.nextafter(budget, 0.0)
    weights = capped_inverse_volatility_weights(
        [row["volatility"] for row in rows],
        [row["max_weight"] for row in rows],
        budget,
    )
    invested = math.fsum(weights)
    stress, breaches = [], []
    for scenario in checked_scenarios:
        # Finite floats have bounded decimal exponents. Local precision covers
        # their entire product/sum range and avoids false breaches at a limit.
        with localcontext(Context(prec=1100, Emin=-1000, Emax=1000)):
            exact = {
                row["symbol"]: Decimal(str(weight))
                * Decimal(str(scenario["shocks"][row["symbol"]]))
                for row, weight in zip(rows, weights)
            }
            exact_return = sum(exact.values(), Decimal(0))
            exact_loss = max(Decimal(0), -exact_return)
            breached = loss_limit is not None and exact_loss > Decimal(str(loss_limit))
            contributions = {symbol: float(value) for symbol, value in exact.items()}
            portfolio_return, loss = float(exact_return), float(exact_loss)
        if not math.isfinite(portfolio_return):
            raise ValueError("scenario_numeric_overflow")
        if breached:
            breaches.append(scenario["id"])
        stress.append(
            {
                "id": scenario["id"],
                "contributions": contributions,
                "portfolio_return": portfolio_return,
                "portfolio_return_decimal": str(exact_return),
                "loss": loss,
                "loss_decimal": str(exact_loss),
                "limit_breached": breached,
            }
        )
    inputs = {
        "assets": rows,
        "cash_reserve": reserve,
        "max_weight": cap,
        "scenarios": checked_scenarios,
        "max_scenario_loss": loss_limit,
    }
    fingerprint = hashlib.sha256(
        json.dumps(inputs, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return {
        "schema": "osiris.portfolio_allocation_preview.v1",
        "input_sha256": fingerprint,
        "method": "capped_inverse_volatility_baseline",
        "optimality_claim": False,
        "allocations": [{**row, "weight": weight} for row, weight in zip(rows, weights)],
        "invested_weight": invested,
        "cash_weight": max(0.0, 1.0 - invested),
        "cash_reserve": reserve,
        "unallocated_due_to_caps": max(0.0, budget - math.fsum(row["max_weight"] for row in rows)),
        "scenarios": stress,
        "max_scenario_loss": loss_limit,
        "scenario_policy": "not_evaluated"
        if loss_limit is None
        else ("breach" if breaches else "pass"),
        "breached_scenarios": breaches,
        "cash_scenario_return_assumption": 0.0,
        "input_provenance": "caller_supplied_not_independently_verified",
        "future_drawdown_guaranteed": False,
        "risk_approval_granted": False,
        "live_execution": False,
        "broker_order_submitted": False,
    }
