import unittest
from check_release_readiness import REQUIRED, assess

class ReleaseGateTests(unittest.TestCase):
    def test_missing_evidence_blocks(self):
        self.assertFalse(assess({"schema": "osiris.release_evidence.v1", "checks": {}})["ready"])

    def test_partial_evidence_blocks(self):
        report = assess({"schema": "osiris.release_evidence.v1", "checks": {REQUIRED[0]: True}})
        self.assertIn("paper_ledger_reconciliation", report["missing"])

    def test_explicit_checks_pass(self):
        self.assertTrue(assess({"schema": "osiris.release_evidence.v1", "checks": {x: True for x in REQUIRED}})["ready"])

    def test_string_true_does_not_pass(self):
        self.assertFalse(assess({"schema": "osiris.release_evidence.v1", "checks": {x: "true" for x in REQUIRED}})["ready"])

if __name__ == "__main__":
    unittest.main()
