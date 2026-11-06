"""Offline checks for sampled outage recovery gate."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "outage", Path(__file__).with_name("check_p2p_outage_recovery.py")
)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def sample(ms, connected, delivered):
    return {"elapsed_ms": ms, "connected": connected, "delivered": delivered}


class OutageTests(unittest.TestCase):
    def test_recovers_within_declared_limit(self):
        data = {"samples": [sample(0, True, True), sample(100, False, False),
                            sample(200, False, False), sample(500, True, True)]}
        result = gate.analyze(data, 100, 500)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["sampled_recovery_ms"], 400)
        self.assertFalse(result["continuous_availability_verified"])

    def test_late_recovery_fails(self):
        data = {"samples": [sample(0, True, True), sample(100, False, False),
                            sample(1000, True, True)]}
        self.assertEqual(gate.analyze(data, 100, 500)["status"], "fail")

    def test_no_recovery_fails(self):
        data = {"samples": [sample(0, True, True), sample(100, False, False)]}
        self.assertFalse(gate.analyze(data, 100, 500)["recovery_observed"])

    def test_missing_baseline_or_outage_rejected(self):
        cases = [
            [sample(0, False, False), sample(100, False, False), sample(200, True, True)],
            [sample(0, True, True), sample(100, True, True)],
        ]
        for samples in cases:
            with self.assertRaises(ValueError):
                gate.analyze({"samples": samples}, 100, 500)

    def test_malformed_or_unsorted_rejected(self):
        cases = [
            [sample(0, True, True), sample(0, False, False)],
            [sample(0, False, True), sample(100, False, False)],
            [sample(0, True, True), sample(100, 1, False)],
        ]
        for samples in cases:
            with self.assertRaises(ValueError):
                gate.analyze({"samples": samples}, 100, 500)


if __name__ == "__main__":
    unittest.main()
