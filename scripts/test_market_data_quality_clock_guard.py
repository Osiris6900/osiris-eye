import unittest
from market_data_quality_clock_guard import inspect_observations


def observation(oid="one", timestamp=1000, price=10.0):
    return {"observation_id": oid, "provider": "test-provider",
            "instrument": "TEST", "observed_at_ms": timestamp, "price": price}


class DataClockTests(unittest.TestCase):
    def test_oversized_integer_price_rejected(self):
        self.assertEqual(inspect_observations([observation(price=10**400)],
                                             now_ms=1000)["status"], "rejected")

    def test_good_research_observation(self):
        result = inspect_observations([observation()], now_ms=1000)
        self.assertTrue(result["all_accepted"])
        self.assertFalse(result["live_trading_authorized"])
    def test_stale(self):
        result = inspect_observations([observation()], now_ms=7000)
        self.assertIn("stale_observation", result["findings"][0]["flags"])
    def test_future_skew(self):
        result = inspect_observations([observation(timestamp=3000)], now_ms=1000)
        self.assertIn("future_clock_skew", result["findings"][0]["flags"])
    def test_duplicate(self):
        result = inspect_observations([observation(), observation()], now_ms=1000)
        self.assertIn("duplicate_observation_id", result["findings"][1]["flags"])
    def test_nan_rejected(self):
        self.assertEqual(inspect_observations([observation(price=float("nan"))],
                                             now_ms=1000)["status"], "rejected")
    def test_boolean_timestamp_rejected(self):
        self.assertEqual(inspect_observations([observation(timestamp=True)],
                                             now_ms=1000)["status"], "rejected")
    def test_untrusted_clock_configuration_rejected(self):
        self.assertEqual(inspect_observations([observation()], now_ms=True)["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
