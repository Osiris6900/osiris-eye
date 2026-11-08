import hashlib
import tempfile
import unittest
from pathlib import Path
from check_master_integration import GATES, validate

SHA = "a" * 40

class MasterIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        gates = {}
        for name in GATES:
            artifact = f"{name}.txt"
            path = self.root / artifact
            path.write_text(f"independent evidence for {name}", encoding="utf-8")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            gates[name] = {"passed": True, "commit_sha": SHA,
                           "artifact": artifact, "sha256": digest}
        self.record = {"commit_sha": SHA, "live_trading_enabled": False,
                       "gates": gates}

    def test_valid_shape_and_digests(self):
        self.assertEqual(validate(self.record, self.root), [])

    def test_missing_gate_fails(self):
        del self.record["gates"]["security_review"]
        self.assertTrue(validate(self.record, self.root))

    def test_wrong_commit_fails(self):
        self.record["gates"]["kill_switch"]["commit_sha"] = "b" * 40
        self.assertTrue(validate(self.record, self.root))

    def test_false_gate_fails(self):
        self.record["gates"]["risk_limits"]["passed"] = False
        self.assertTrue(validate(self.record, self.root))

    def test_tampered_artifact_fails(self):
        self.root.joinpath("risk_limits.txt").write_text("tampered", encoding="utf-8")
        self.assertTrue(validate(self.record, self.root))

    def test_path_traversal_fails(self):
        self.record["gates"]["operator_rbac"]["artifact"] = "../outside"
        self.assertTrue(validate(self.record, self.root))

    def test_reused_artifact_fails(self):
        self.record["gates"]["risk_limits"]["artifact"] = "kill_switch.txt"
        self.record["gates"]["risk_limits"]["sha256"] = self.record["gates"]["kill_switch"]["sha256"]
        self.assertTrue(validate(self.record, self.root))

    def test_oversized_artifact_fails(self):
        path = self.root / "risk_limits.txt"
        path.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
        self.record["gates"]["risk_limits"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertTrue(validate(self.record, self.root))

    def test_live_trading_fails(self):
        self.record["live_trading_enabled"] = True
        self.assertTrue(validate(self.record, self.root))

if __name__ == "__main__":
    unittest.main()
