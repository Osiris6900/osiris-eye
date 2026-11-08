"""Regression checks for offline workflow reference inventory."""
import tempfile
import unittest
from pathlib import Path
from scripts.audit_ci_action_pins import audit, classify


class PinAuditTests(unittest.TestCase):
    def test_classifies_references(self):
        self.assertEqual(classify("./.github/actions/local"), "local")
        self.assertEqual(classify("actions/checkout@" + "a" * 40), "pinned")
        self.assertEqual(classify("actions/checkout@v4"), "mutable")
        self.assertEqual(classify("docker://alpine@sha256:" + "a" * 64), "pinned")
        self.assertEqual(classify("docker://alpine:latest"), "mutable")
        self.assertEqual(classify("actions/checkout"), "invalid")

    def test_scans_job_and_step_uses(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ci.yml"
            path.write_text("jobs:\n  reusable:\n    uses: org/repo/.github/workflows/test.yml@main\n"
                            "  build:\n    steps:\n      - uses: actions/checkout@v4 # comment\n"
                            "      - uses: actions/setup-node@" + "a" * 40 + "\n", encoding="utf-8")
            report = audit(Path(temp))
            self.assertEqual(report["counts"]["mutable"], 2)
            self.assertEqual(report["counts"]["pinned"], 1)


if __name__ == "__main__":
    unittest.main()
