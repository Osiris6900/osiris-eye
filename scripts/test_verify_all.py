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
        for name in ("rust-native", "native-process", "peer-receipts", "browser", "recovery-intakes", "local-paper-service-dr"):
            self.assertIn("PLAN " + name + ":", result.stdout)
        self.assertNotIn("PLAN release-strict:", result.stdout)
        for name in ("dependency-advisories", "python-lock", "license-provenance"):
            self.assertIn("PLAN " + name + ":", result.stdout)
        self.assertIn("--junitxml", result.stdout)

    def test_both_profiles_exercise_release_evidence_contracts(self):
        module = load_module()
        for checks in (module.CHECKS, module.ENGINEERING_CHECKS):
            with self.subTest(checks=checks):
                commands = {name: command for name, command, _ in checks}
                self.assertEqual(commands["release-evidence-contracts"],
                    ["python", "-m", "unittest", "discover", "-s", "scripts",
                     "-p", "test_check_release*.py"])
        passed = [{"name": name, "status": "passed"} for name, _, _ in module.ENGINEERING_CHECKS]
        source = {"git_head": "a" * 40, "worktree_sha256": "b" * 64}
        missing = module.engineering_report(
            [row for row in passed if row["name"] != "release-evidence-contracts"], source, source)
        self.assertFalse(missing["all_passed"])
        self.assertFalse(missing["release_gates"]["T20"]["local_checks_passed"])

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
        for name in ("dependency-advisories", "python-lock", "license-provenance"):
            missing = module.engineering_report([row for row in passed if row["name"] != name], source, source)
            self.assertFalse(missing["release_gates"]["T01"]["local_checks_passed"])
            self.assertFalse(missing["all_passed"])
        without_trace = module.engineering_report([row for row in passed if row["name"] != "receipt-paper"], source, source)
        self.assertFalse(without_trace["release_gates"]["T19"]["local_checks_passed"])
        self.assertFalse(without_trace["all_passed"])
        without_integrity = module.engineering_report([row for row in passed if row["name"] != "receipt-artifact-integrity"], source, source)
        self.assertFalse(without_integrity["all_passed"])
        for gate in ("T13", "T14", "T15", "T16", "T19"):
            self.assertFalse(without_integrity["release_gates"][gate]["local_checks_passed"])
        without_service_dr = module.engineering_report(
            [row for row in passed if row["name"] != "local-paper-service-dr"], source, source)
        self.assertFalse(without_service_dr["all_passed"])
        self.assertFalse(without_service_dr["release_gates"]["T18"]["local_checks_passed"])
        self.assertFalse(without_service_dr["workstreams"]["W18"]["engineering_checks_passed"])
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

    def test_actual_pytest_skip_is_not_reported_as_complete(self):
        module = load_module()
        for source, status in (
            ("def test_run(): assert True\n", "passed"),
            ("import pytest\ndef test_run(): pytest.skip('binary missing')\n", "failed"),
            ("import pytest\n@pytest.mark.xfail(reason='unverified')\ndef test_run(): assert False\n", "failed"),
        ):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                (path / "test_case.py").write_text(source)
                junit = path / "tests.xml"
                result = module.run_check("python-tests", [sys.executable, "-m", "pytest",
                    "-q", str(path / "test_case.py"), "--junitxml", str(junit)], None, 30)
                self.assertEqual(result["exit_code"], 0, result)
                checked = module.require_complete_pytest(result, junit)
                self.assertEqual(checked["status"], status, checked)
                self.assertEqual(checked["test_evidence"]["test_cases"], 1)
                self.assertEqual(len(checked["test_evidence"]["sha256"]), 64)

    def test_missing_empty_or_malformed_junit_never_passes(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "tests.xml"
            passed = {"name": "python-tests", "status": "passed"}
            self.assertEqual(module.require_complete_pytest(passed, report)["status"], "failed")
            for document in ("not XML", "<testsuites/>", "<unknown><testcase/></unknown>",
                '<testsuites><testsuite><testcase><failure/></testcase></testsuite></testsuites>',
                '<testsuites><testsuite><testcase><error/></testcase></testsuite></testsuites>'):
                report.write_text(document)
                with self.subTest(document=document):
                    self.assertEqual(module.require_complete_pytest(passed, report)["status"], "failed")
            failed = {"name": "python-tests", "status": "failed", "exit_code": 3}
            self.assertEqual(module.require_complete_pytest(failed, report), failed)


if __name__ == "__main__":
    unittest.main()
