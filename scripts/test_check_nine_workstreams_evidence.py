import hashlib
import tempfile
import unittest
from pathlib import Path
from check_nine_workstreams_evidence import WORKSTREAMS, evaluate


class NineWorkstreamTests(unittest.TestCase):
    def test_empty_manifest_blocked(self):
        self.assertEqual(evaluate({}, Path("."))["status"], "blocked")
    def test_missing_workstream_blocked(self):
        self.assertIn("missing_or_extra_workstreams", evaluate({
            "commit_sha": "a" * 40, "workstreams": {}}, Path("."))["failures"])
    def test_valid_digests_do_not_claim_independent_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "evidence.txt").write_text("fixture")
            digest = hashlib.sha256(b"fixture").hexdigest()
            entries = {k: {"issue": issue, "artifact": "evidence.txt",
                           "sha256": digest, "reviewed": True}
                       for k, issue in WORKSTREAMS.items()}
            result = evaluate({"commit_sha": "a" * 40, "workstreams": entries}, root)
            self.assertEqual(result["status"], "evidence_present_not_independently_verified")
            self.assertFalse(result["live_trading_authorized"])
            self.assertFalse(result["independent_acceptance_verified"])
    def test_traversal_rejected(self):
        entries = {k: {"issue": issue, "artifact": "../secret",
                       "sha256": "a" * 64, "reviewed": True}
                   for k, issue in WORKSTREAMS.items()}
        result = evaluate({"commit_sha": "a" * 40, "workstreams": entries}, Path("."))
        self.assertIn("dashboard:invalid_artifact_reference", result["failures"])
    def test_unreviewed_blocked(self):
        entries = {k: {"issue": issue, "artifact": "missing",
                       "sha256": "a" * 64, "reviewed": False}
                   for k, issue in WORKSTREAMS.items()}
        result = evaluate({"commit_sha": "a" * 40, "workstreams": entries}, Path("."))
        self.assertIn("dashboard:review_pending", result["failures"])


if __name__ == "__main__":
    unittest.main()
