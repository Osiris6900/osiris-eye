import csv
from pathlib import Path

from osiris_api.risk import RiskLimits, check_order


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
            actual = "approved" if allowed else reason
            assert actual == expected, f"vector mismatch: {row}"
            cases += 1
    assert cases >= 10


def test_python_risk_engine_rejects_bool_and_non_numeric_inputs():
    assert check_order(
        quantity=True, price=10, current_position_notional=0, daily_pnl=0
    ) == (False, "non_finite_input")
    assert check_order(
        quantity=1, price=10, current_position_notional=0, daily_pnl=False
    ) == (False, "non_finite_input")
    assert check_order(
        quantity="1", price=10, current_position_notional=0, daily_pnl=0
    ) == (False, "non_finite_input")


def test_python_risk_engine_rejects_unrepresentable_integer():
    assert check_order(
        quantity=10**400, price=1, current_position_notional=0, daily_pnl=0
    ) == (False, "non_finite_input")
