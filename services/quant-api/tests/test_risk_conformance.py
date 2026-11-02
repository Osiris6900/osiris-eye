import csv
from pathlib import Path

from osiris_api.risk import RiskLimits, check_order


CANONICAL_REASON = {
    "approved_for_simulation_only": "approved",
    "invalid_numeric_input": "non_finite_input",
    "invalid_order_or_position": "invalid_order",
    "invalid_risk_limits": "invalid_order",
    "max_order_notional_exceeded": "order_limit",
    "max_position_notional_exceeded": "position_limit",
    "daily_loss_limit_reached": "daily_loss_limit",
}


def test_shared_python_rust_risk_conformance_vectors():
    fixture = Path(__file__).resolve().parents[3] / "tests" / "risk_conformance.csv"
    cases = 0
    with fixture.open(newline="", encoding="utf-8") as handle:
        for row in csv.reader(line for line in handle if line.strip() and not line.startswith("#")):
            quantity, price, position, daily_pnl, max_order, max_position, max_loss = (
                float(value) for value in row[:7]
            )
            expected = row[7]
            allowed, reason = check_order(
                quantity=quantity,
                price=price,
                current_position_notional=position,
                daily_pnl=daily_pnl,
                limits=RiskLimits(max_order, max_position, max_loss),
            )
            actual = CANONICAL_REASON[reason]
            assert actual == expected, f"vector mismatch: {row}"
            assert allowed is (expected == "approved")
            cases += 1
    assert cases >= 10


def test_python_risk_engine_rejects_bool_and_non_numeric_inputs():
    assert check_order(
        quantity=True, price=10, current_position_notional=0, daily_pnl=0
    ) == (False, "invalid_numeric_input")
    assert check_order(
        quantity=1, price=10, current_position_notional=0, daily_pnl=False
    ) == (False, "invalid_numeric_input")
    assert check_order(
        quantity="1", price=10, current_position_notional=0, daily_pnl=0
    ) == (False, "invalid_numeric_input")


def test_python_risk_engine_rejects_unrepresentable_integer():
    assert check_order(
        quantity=10**400, price=1, current_position_notional=0, daily_pnl=0
    ) == (False, "invalid_numeric_input")
