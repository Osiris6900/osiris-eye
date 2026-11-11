"""Executable harness isolation and honest failure-evidence checks."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run_market_paper_acceptance.py"


class MarketPaperAcceptanceTests(unittest.TestCase):
    def test_offline_runner_preserves_operator_database_and_hashes_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            operator = root / "operator.sqlite3"
            operator.write_bytes(b"operator state must not change")
            output = root / "evidence.json"
            env = dict(os.environ, OSIRIS_PAPER_EXECUTION_DB=str(operator))
            env.pop("OSIRIS_ENABLE_LIVE_EXECUTION", None)
            process = subprocess.run([sys.executable, str(RUNNER), "--output", str(output)],
                                     cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            evidence = json.loads(output.read_text())
            self.assertTrue(evidence["passed"])
            self.assertEqual(evidence["mode"], "offline_fixture")
            self.assertFalse(evidence["release_gate_accepted"])
            self.assertEqual(evidence["final_account"]["cash"], "99988")
            self.assertEqual(operator.read_bytes(), b"operator state must not change")
            self.assertIn("separate_process_restart_recovers_exact_ledger_state", evidence["checks"])
            digest = hashlib.sha256(output.read_bytes()).hexdigest()
            self.assertEqual(output.with_suffix(".json.sha256").read_text(), f"{digest}  evidence.json\n")

    def test_live_execution_environment_refused_with_failed_report(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "failure.json"
            env = dict(os.environ, OSIRIS_ENABLE_LIVE_EXECUTION="1")
            process = subprocess.run([sys.executable, str(RUNNER), "--output", str(output)],
                                     cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(process.returncode, 0)
            evidence = json.loads(output.read_text())
            self.assertFalse(evidence["passed"])
            self.assertEqual(evidence["error"]["message"], "live_execution_environment_refused")
            self.assertFalse(evidence["live_execution_enabled"])
            self.assertEqual(evidence["runs"], [])


if __name__ == "__main__":
    unittest.main()
