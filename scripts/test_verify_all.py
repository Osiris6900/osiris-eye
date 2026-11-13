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
    def test_engineering_plan_includes_native_browser_and_actual_peer_receipts(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--profile", "engineering"],
                                cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("rust-native", "native-process", "peer-receipts", "browser", "recovery-intakes"):
            self.assertIn("PLAN " + name + ":", result.stdout)
        self.assertNotIn("PLAN release-strict:", result.stdout)

    def test_workstream_report_requires_every_check_and_unchanged_sources(self):
        module = load_module()
        passed = [{"name": name, "status": "passed"} for name, _, _ in module.ENGINEERING_CHECKS]
        source = {"git_head": "a" * 40, "worktree_sha256": "b" * 64}
        report = module.engineering_report(passed, source, source)
        self.assertEqual(set(report["workstreams"]), {"W%02d" % index for index in range(1, 21)})
        self.assertTrue(report["all_passed"])
        self.assertFalse(report["release_accepted"])
        self.assertFalse(report["merge_authorized"])
        self.assertEqual(set(report["release_gates"]), {"T%02d" % i for i in range(1, 21)})
        self.assertEqual(len(report["remaining_release_gates"]), 17)
        self.assertIn("T06", report["remaining_release_gates"])
        self.assertTrue(all(not row["current_candidate_acceptance_inferred"] for row in report["release_gates"].values()))
        self.assertIn("receipt-paper", report["release_gates"]["T19"]["checks"])
        self.assertIn("supply-chain-sbom", report["release_gates"]["T01"]["checks"])
        without_sbom = module.engineering_report([row for row in passed if row["name"] != "supply-chain-sbom"], source, source)
        self.assertFalse(without_sbom["release_gates"]["T01"]["local_checks_passed"])
        self.assertFalse(without_sbom["all_passed"])
        without_trace = module.engineering_report([row for row in passed if row["name"] != "receipt-paper"], source, source)
        self.assertFalse(without_trace["release_gates"]["T19"]["local_checks_passed"])
        self.assertFalse(without_trace["all_passed"])
        without_integrity = module.engineering_report([row for row in passed if row["name"] != "receipt-artifact-integrity"], source, source)
        self.assertFalse(without_integrity["all_passed"])
        for gate in ("T13", "T14", "T15", "T16", "T19"):
            self.assertFalse(without_integrity["release_gates"][gate]["local_checks_passed"])
        self.assertTrue(all(row["independent_acceptance"] == "pending" for row in report["workstreams"].values()))
        failed = module.engineering_report([row for row in passed if row["name"] != "native-process"], source, source)
        self.assertFalse(failed["all_passed"])
        self.assertFalse(failed["workstreams"]["W20"]["engineering_checks_passed"])
        missing_inventory = module.engineering_report([row for row in passed if row["name"] != "release-inventory"], source, source)
        self.assertFalse(missing_inventory["all_passed"])
        self.assertFalse(missing_inventory["check_inventory_complete"])
        changed = module.engineering_report(passed, source, {**source, "worktree_sha256": "c" * 64})
        self.assertFalse(changed["all_passed"])
        self.assertFalse(changed["source_unchanged"])

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
