import unittest
from advanced_risk_signal_advisors import stress_portfolio, resolve_signals


class AdvancedAdvisoryTests(unittest.TestCase):
    def test_long_crash_loss(self):
        result = stress_portfolio([{"instrument": "A", "signed_notional": 1000}],
                                  {"A": -1000}, 50)
        self.assertEqual(result["scenario_loss"], 100)
        self.assertTrue(result["risk_budget_breached"])
        self.assertFalse(result["live_trading_authorized"])
    def test_short_rally_loss(self):
        result = stress_portfolio([{"instrument": "A", "signed_notional": -1000}],
                                  {"A": 1000}, 50)
        self.assertEqual(result["scenario_loss"], 100)
    def test_missing_shock_fails(self):
        self.assertEqual(stress_portfolio([{"instrument": "A", "signed_notional": 100}],
                                          {}, 10)["status"], "rejected")
    def test_conflicting_signals_blocked(self):
        result = resolve_signals([{"id": "1", "instrument": "A", "direction": "buy"},
                                  {"id": "2", "instrument": "A", "direction": "sell"}])
        self.assertEqual(result["instruments"]["A"]["decision"], "conflict_blocked")
        self.assertEqual(result["orders_placed"], 0)
    def test_single_direction_advisory(self):
        result = resolve_signals([{"id": "1", "instrument": "A", "direction": "buy"}])
        self.assertEqual(result["instruments"]["A"]["decision"], "buy")
        self.assertFalse(result["live_trading_authorized"])
    def test_duplicate_id_rejected(self):
        result = resolve_signals([{"id": "1", "instrument": "A", "direction": "buy"},
                                  {"id": "1", "instrument": "B", "direction": "buy"}])
        self.assertEqual(result["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
