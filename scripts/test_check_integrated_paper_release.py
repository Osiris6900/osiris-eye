import unittest
from check_integrated_paper_release import REQUIRED, validate

class IntegratedPaperReleaseTests(unittest.TestCase):
    def record(self):
        return {**{key: True for key in REQUIRED}, "live_trading_enabled": False}
    def test_complete_evidence_shape(self):
        self.assertEqual(validate(self.record()), [])
    def test_each_missing_gate_fails_closed(self):
        for key in REQUIRED:
            record = self.record()
            record[key] = False
            self.assertTrue(validate(record), key)
    def test_live_trading_denied(self):
        record = self.record()
        record["live_trading_enabled"] = True
        self.assertTrue(validate(record))
    def test_nonobject_denied(self):
        self.assertTrue(validate([]))

if __name__ == "__main__":
    unittest.main()
