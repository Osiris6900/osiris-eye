"""Shared Python/Rust vectors for the paper execution gate."""
import csv
from pathlib import Path

from osiris_api.risk import RiskLimits, check_execution


CANONICAL = {
    "approved_for_simulation_only": "approved",
    "invalid_numeric_input": "non_finite_input",
    "invalid_order_or_position": "invalid_order",
    "invalid_risk_limits": "invalid_order",
    "max_order_notional_exceeded": "order_limit",
    "max_position_notional_exceeded": "position_limit",
    "exposure_budget_exceeded": "exposure_budget",
    "daily_loss_limit_reached": "daily_loss_limit",
    "kill_switch_active": "kill_switch_active",
    "stale_market_data": "stale_market_data",
    "risk_service_unavailable": "risk_service_unavailable",
}

PRETRADE = {"kill_switch_active", "stale_market_data", "risk_service_unavailable"}


def test_shared_python_rust_execution_conformance_vectors():
    fixture = Path(__file__).resolve().parents[3] / "tests" / "risk_execution_conformance.csv"
    cases = 0
    with fixture.open(newline="", encoding="utf-8") as handle:
        rows = csv.reader(line for line in handle if line.strip() and not line.startswith("#"))
        for row in rows:
            (
                side, quantity, price, position, gross, daily_pnl, max_order, max_position,
                max_loss, max_gross, kill, fresh, risk_up, expected,
            ) = row
            approved, reason, limits_evaluated = check_execution(
                side=side,
                quantity=float(quantity),
                price=float(price),
                current_position_notional=float(position),
                current_gross_exposure=float(gross),
                daily_pnl=float(daily_pnl),
                limits=RiskLimits(
                    max_order_notional=float(max_order),
                    max_position_notional=float(max_position),
                    max_daily_loss=float(max_loss),
                    max_gross_exposure=float(max_gross),
                ),
                kill_switch_active=kill == "1",
                market_data_fresh=fresh == "1",
                risk_service_available=risk_up == "1",
            )
            assert CANONICAL[reason] == expected, row
            assert approved is (expected == "approved")
            assert limits_evaluated is (expected not in PRETRADE)
            cases += 1
    assert cases >= 15
