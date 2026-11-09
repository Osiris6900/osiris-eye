#!/usr/bin/env python3
"""Planner tests for scripts/verify_all.py. They do not run the full suite."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_all.py"


def load_module():
    spec = importlib.util.spec_from_file_location("verify_all", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VerifyAllTests(unittest.TestCase):
    def test_default_is_dry_run_and_lists_risk_and_paper_checks(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PLAN python-tests:", result.stdout)
        self.assertIn("services/quant-api/tests", result.stdout)
        self.assertIn("PLAN rust-risk:", result.stdout)
        self.assertIn("crates/risk-kernel/Cargo.toml", result.stdout)
        self.assertNotIn("live_trading_authorized", result.stdout)

    def test_missing_tools_fail_closed_without_authorizing_a_merge(self):
        env = os.environ.copy()
        env["PATH"] = str(ROOT / "does-not-exist")
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "verification.json"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--execute", "--report", str(report_path)],
                cwd=ROOT, capture_output=True, text=True, check=False, env=env,
            )
            self.assertEqual(result.returncode, 1, result.stdout)
            report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertFalse(report["all_passed"])
        self.assertFalse(report["live_trading_authorized"])
        self.assertFalse(report["merge_authorized"])
        self.assertTrue(all(item["status"] == "blocked" for item in report["checks"]))

    def test_simulated_command_failure_is_not_a_pass(self):
        module = load_module()
        failed = module.run_check(
            "simulated-failure",
            [sys.executable, "-c", "import sys; sys.exit(3)"],
            None,
            30,
        )
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["exit_code"], 3)
        report = module.build_report([failed])
        self.assertFalse(report["all_passed"])
        self.assertFalse(report["live_trading_authorized"])
        self.assertFalse(report["merge_authorized"])

    def test_existing_report_is_not_overwritten(self):
        env = os.environ.copy()
        env["PATH"] = str(ROOT / "does-not-exist")
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "verification.json"
            report_path.write_text("keep\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--execute", "--report", str(report_path)],
                cwd=ROOT, capture_output=True, text=True, check=False, env=env,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(report_path.read_text(encoding="utf-8"), "keep\n")

    def test_nonpositive_timeout_is_rejected(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--timeout", "0"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("positive", result.stderr)


if __name__ == "__main__":
    unittest.main()
