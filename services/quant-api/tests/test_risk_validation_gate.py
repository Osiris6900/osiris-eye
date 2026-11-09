"""Unit tests for standalone risk gate; no live trading integration."""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from osiris_api.risk.input_validator import OrderInputValidator
from osiris_api.risk.engine import RiskEngine

GOOD = {"order_id": "order-1", "symbol": "BTCUSD", "side": "buy",
        "quantity": 1.0, "price": 100.0}


class RiskValidationTests(unittest.TestCase):
    def test_valid_input(self):
        self.assertEqual(OrderInputValidator.validate(GOOD, 0), [])

    def test_bad_market_ages(self):
        for age in (None, -1, math.nan, math.inf, -math.inf, "0", True):
            with self.subTest(age=age):
                self.assertFalse(OrderInputValidator.is_valid(
                    OrderInputValidator.validate(GOOD, age)))

    def test_bad_quantity_and_price(self):
        for field in ("quantity", "price"):
            for value in (None, 0, -1, math.nan, math.inf, "10", True):
                with self.subTest(field=field, value=value):
                    self.assertTrue(OrderInputValidator.validate({**GOOD, field: value}, 0))

    def test_missing_identity(self):
        for field in ("order_id", "symbol", "side"):
            order = {**GOOD}
            del order[field]
            self.assertTrue(OrderInputValidator.validate(order, 0))

    def test_fail_closed(self):
        engine = RiskEngine({"stale_data_threshold_seconds": 30})
        self.assertFalse(engine.check_order(GOOD, 0).allowed)
        engine.kill_switch_active = False
        self.assertFalse(engine.check_order(GOOD, 31).allowed)
        self.assertFalse(engine.check_order(GOOD, 0).allowed)

    def test_invalid_config(self):
        for value in (0, -1, math.nan, math.inf, "30"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                RiskEngine({"stale_data_threshold_seconds": value})


if __name__ == "__main__":
    unittest.main()
