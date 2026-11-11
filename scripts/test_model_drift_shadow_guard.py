import unittest
from model_drift_shadow_guard import population_stability_index as psi


class DriftTests(unittest.TestCase):
    def test_oversized_counts_and_threshold_are_rejected(self):
        self.assertEqual(psi([10**400, 100], [100, 10**400])["status"], "rejected")
        self.assertEqual(psi([100, 100], [100, 100],
                             warning_threshold=10**400)["status"], "rejected")

    def test_identical_distribution(self):
        result = psi([100, 100], [100, 100])
        self.assertEqual(result["psi"], 0)
        self.assertFalse(result["warning"])
        self.assertFalse(result["live_trading_authorized"])
    def test_distribution_shift(self):
        result = psi([900, 100], [100, 900])
        self.assertTrue(result["warning"])
    def test_zero_bin_safe(self):
        self.assertEqual(psi([100, 0], [0, 100])["status"], "drift_advisory")
    def test_small_sample_rejected(self):
        self.assertEqual(psi([10, 10], [10, 10])["status"], "rejected")
    def test_negative_count_rejected(self):
        self.assertEqual(psi([100, -1], [100, 100])["status"], "rejected")
    def test_boolean_count_rejected(self):
        self.assertEqual(psi([True, 100], [100, 100])["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
