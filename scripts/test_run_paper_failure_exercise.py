"""Actual isolated paper-ledger failure-injection regression tests."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from check_paper_failure_matrix import validate
from run_paper_failure_exercise import run_failure_matrix

SHA = "f" * 40
URL = "https://github.com/Osiris6900/osiris-eye/actions/runs/123456789"


class PaperFailureExerciseTests(unittest.TestCase):
    def test_real_paper_code_blocks_all_three_fault_windows(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            report = run_failure_matrix(output, commit_sha=SHA, ci_run_url=URL)
            self.assertEqual(validate(report), [])
            self.assertTrue(report["actual_paper_ledger_code_invoked"])
            self.assertFalse(report["production_failover_tested"])
            self.assertFalse(report["t16_gate_accepted"])
            self.assertFalse(report["broker_order_submitted"])
            self.assertEqual(set(report["scenarios"]), {
                "kill_switch", "restart_recovery", "stale_data_rejection",
            })
            self.assertEqual(
                report["details"]["kill_switch"]["blocked_reason"],
                "kill_switch_active",
            )
            self.assertEqual(
                report["details"]["stale_data_rejection"]["blocked_reason"],
                "stale_market_data",
            )
            self.assertEqual(
                report["details"]["restart_recovery"][
                    "authorized_fills_after_verified_recovery"
                ], 1,
            )
            self.assertTrue((output / "sha256sum.txt").is_file())
            self.assertEqual(
                json.loads((output / "paper-failure-matrix.json").read_text()),
                report,
            )

    def test_bad_sha_or_fake_run_link_fails_before_fixture(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "exact_commit_required"):
                run_failure_matrix(Path(folder), commit_sha="main", ci_run_url=URL)
            with self.assertRaisesRegex(ValueError, "actual_ci_run_url_required"):
                run_failure_matrix(
                    Path(folder), commit_sha=SHA, ci_run_url="https://example.com",
                )

    def test_invalid_report_cannot_be_marked_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            report = run_failure_matrix(Path(folder), commit_sha=SHA, ci_run_url=URL)
            report["scenarios"]["kill_switch"]["orders_accepted_after_failure"] = 1
            self.assertTrue(validate(report))


if __name__ == "__main__":
    unittest.main()
