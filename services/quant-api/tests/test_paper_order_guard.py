from decimal import Decimal

import pytest

from osiris_api.paper_order_guard import Decision, PaperOrder, PaperOrderGuard


def order(identifier="one", quantity="2", price="10"):
    return PaperOrder(identifier, "TEST", Decimal(quantity), Decimal(price))


def test_defaults_to_halted():
    guard = PaperOrderGuard(Decimal("100"))
    assert guard.evaluate(order(), risk_approved=True).reason == "paper_halted"


def test_requires_risk_approval():
    guard = PaperOrderGuard(Decimal("100"))
    guard.set_paper_enabled(True)
    assert guard.evaluate(order(), risk_approved=False).reason == "risk_not_approved"


def test_idempotent_replay_and_conflict():
    guard = PaperOrderGuard(Decimal("100"))
    guard.set_paper_enabled(True)
    assert guard.evaluate(order(), risk_approved=True).decision == Decision.ACCEPTED
    assert guard.evaluate(order(), risk_approved=True).decision == Decision.REPLAY
    assert guard.evaluate(order(quantity="3"), risk_approved=True).reason == "idempotency_conflict"


@pytest.mark.parametrize("quantity,price", [
    ("0", "10"), ("-1", "10"), ("1", "0"), ("NaN", "10"),
    ("Infinity", "1"), ("11", "10"),
])
def test_rejects_invalid_or_excess_notional(quantity, price):
    guard = PaperOrderGuard(Decimal("100"))
    guard.set_paper_enabled(True)
    assert guard.evaluate(order(quantity=quantity, price=price), risk_approved=True).decision == Decision.REJECTED


def test_halt_prevents_new_orders():
    guard = PaperOrderGuard(Decimal("100"))
    guard.set_paper_enabled(True)
    guard.set_paper_enabled(False)
    assert guard.evaluate(order(), risk_approved=True).reason == "paper_halted"
