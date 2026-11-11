"""Negative regression tests for cross-runner DR report integrity."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from verify_cross_runner_dr_artifacts import verify

COMMIT = "a" * 40
URL = "https://github.com/Osiris6900/osiris-eye/actions/runs/12345"
NAME = "paper-ci-cross-job-restore.json"


def fixture():
    return {
        "schema": "osiris.cross-runner-paper-restore.v1",
        "status": "passed",
        "scope": "ci-artifact-transfer-between-separate-github-jobs",
        "source_commit_sha": COMMIT,
        "source_github_run_url": URL,
        "backup_sha256": "b" * 64,
        "restored_initial_fill_count": 1,
        "continued_fill_count": 2,
        "restored_ledger_reconciled": True,
        "artifact_backup_transport_tested": True,
        "independent_physical_hosts_verified": False,
        "production_service_recovery_verified": False,
        "rto_slo_certified": False,
        "rpo_slo_certified": False,
        "live_execution": False,
        "broker_order_submitted": False,
        "t18_release_gate_accepted": False,
    }


def write(directory, data):
    raw = (json.dumps(data, sort_keys=True) + "\n").encode()
    (directory / NAME).write_bytes(raw)
    (directory / (NAME + ".sha256")).write_text(
        hashlib.sha256(raw).hexdigest() + "  " + NAME + "\n", encoding="utf-8"
    )


class CrossRunnerDrVerifierTests(unittest.TestCase):
    def test_malformed_fill_counts_return_errors_without_crashing(self):
        for initial in ("1", None, True, [], {}):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as folder:
                path = Path(folder)
                data = fixture()
                data["restored_initial_fill_count"] = initial
                write(path, data)
                errors = verify(path, expected_commit=COMMIT, expected_run_url=URL)
                self.assertIn("invalid_initial_fill_count", errors)
                self.assertIn("invalid_continuation_count", errors)

    def test_valid_synthetic_report_is_only_offline_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            write(path, fixture())
            self.assertEqual(verify(path, expected_commit=COMMIT, expected_run_url=URL), [])

    def test_mutated_report_and_manifest_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            write(path, fixture())
            (path / NAME).write_text("{}")
            self.assertIn("manifest_mismatch", verify(path, expected_commit=COMMIT, expected_run_url=URL))

    def test_forged_release_and_live_flags_fail(self):
        for key, value in (
            ("t18_release_gate_accepted", True),
            ("independent_physical_hosts_verified", True),
            ("live_execution", True),
            ("continued_fill_count", 9),
            ("source_commit_sha", "c" * 40),
            ("source_github_run_url", URL + "0"),
        ):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as folder:
                path = Path(folder)
                data = fixture()
                data[key] = value
                write(path, data)
                self.assertTrue(verify(path, expected_commit=COMMIT, expected_run_url=URL))

    def test_extra_file_and_invalid_candidate_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            write(path, fixture())
            self.assertTrue(verify(path, expected_commit="invalid", expected_run_url=URL))
            (path / "unexpected").write_text("extra")
            self.assertIn("missing_or_extra_artifact", verify(path, expected_commit=COMMIT, expected_run_url=URL))


if __name__ == "__main__":
    unittest.main()
