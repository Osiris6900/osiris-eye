import hashlib
import tempfile
import unittest
from pathlib import Path
from check_cross_module_acceptance import STAGES, validate

SHA = "a" * 40
URL = "https://github.com/Osiris6900/osiris-eye/actions/runs/123"

class CrossModuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.report = {"commit_sha": SHA, "live_execution_enabled": False, "stages": {}}
        previous = None
        for i, name in enumerate(STAGES):
            rel = f"{name}.json"
            content = f'{{"receipt_id":"receipt-{i}"}}'.encode()
            (self.root / rel).write_bytes(content)
            stage = {"passed": True, "commit_sha": SHA, "ci_run_url": URL,
                     "artifact": rel, "sha256": hashlib.sha256(content).hexdigest(),
                     "output_receipt_id": f"receipt-{i}"}
            if previous is not None:
                stage["input_receipt_id"] = previous
            self.report["stages"][name] = stage
            previous = stage["output_receipt_id"]

    def test_valid_chain(self):
        self.assertEqual(validate(self.report, self.root), [])
    def test_missing_stage(self):
        del self.report["stages"]["paper_execution"]
        self.assertTrue(validate(self.report, self.root))
    def test_receipt_mismatch(self):
        self.report["stages"]["paper_execution"]["input_receipt_id"] = "wrong"
        self.assertTrue(validate(self.report, self.root))
    def test_tampered_artifact(self):
        (self.root / "python_ledger_import.json").write_text("tampered")
        self.assertTrue(validate(self.report, self.root))
    def test_path_traversal(self):
        self.report["stages"]["paper_execution"]["artifact"] = "../outside"
        self.assertTrue(validate(self.report, self.root))
    def test_live_execution(self):
        self.report["live_execution_enabled"] = True
        self.assertTrue(validate(self.report, self.root))

if __name__ == "__main__":
    unittest.main()
