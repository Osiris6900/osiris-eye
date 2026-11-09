import copy
import unittest
from check_paper_failure_matrix import SCENARIOS, validate

SHA = "a" * 40
URL = "https://github.com/Osiris6900/osiris-eye/actions/runs/123"

def sample():
    return {"commit_sha": SHA, "live_execution_enabled": False,
            "scenarios": {name: {"passed": True, "commit_sha": SHA,
                "orders_accepted_after_failure": 0, "unreconciled_orders": 0,
                "test_command": "pytest -q tests/test_failure_matrix.py",
                "ci_run_url": URL} for name in SCENARIOS}}

class FailureMatrixEvidenceTests(unittest.TestCase):
    def test_valid_shape(self):
        self.assertEqual(validate(sample()), [])
    def test_missing_scenario(self):
        r = sample(); del r["scenarios"]["kill_switch"]
        self.assertTrue(validate(r))
    def test_unblocked_order_fails(self):
        r = sample(); r["scenarios"]["stale_data_rejection"]["orders_accepted_after_failure"] = 1
        self.assertTrue(validate(r))
    def test_restart_unreconciled_fails(self):
        r = sample(); r["scenarios"]["restart_recovery"]["unreconciled_orders"] = 1
        self.assertTrue(validate(r))
    def test_live_execution_fails(self):
        r = sample(); r["live_execution_enabled"] = True
        self.assertTrue(validate(r))
    def test_missing_ci_fails(self):
        r = sample(); r["scenarios"]["kill_switch"]["ci_run_url"] = ""
        self.assertTrue(validate(r))

if __name__ == "__main__":
    unittest.main()
