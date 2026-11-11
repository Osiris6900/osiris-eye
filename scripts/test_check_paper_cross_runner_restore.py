"""Negative and actual-code-path tests of portable synthetic paper DR."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.check_paper_cross_runner_restore import restore_and_verify
from scripts.run_paper_dr_exercise import run


class PortableRecoveryTests(unittest.TestCase):
    def test_backup_is_recovered_by_real_ledger_code_and_can_continue(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            proof = Path(directory) / "cross-job.json"
            with patch.dict(os.environ, {"OSIRIS_PAPER_EXECUTION_DB": "existing-operator-db"}, clear=False):
                # Producer does not restore this env; isolate its side effects.
                try:
                    original = run(source)
                    os.environ["OSIRIS_PAPER_EXECUTION_DB"] = "existing-operator-db"
                    result = restore_and_verify(source, proof)
                    self.assertEqual(os.environ["OSIRIS_PAPER_EXECUTION_DB"], "existing-operator-db")
                finally:
                    os.environ["OSIRIS_PAPER_EXECUTION_DB"] = "existing-operator-db"
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["restored_initial_fill_count"], original["restored_fill_count"])
            self.assertEqual(result["continued_fill_count"], original["restored_fill_count"] + 1)
            self.assertFalse(result["t18_release_gate_accepted"])
            self.assertFalse(result["independent_physical_hosts_verified"])
            self.assertEqual(json.loads(proof.read_text()), result)
            self.assertTrue(proof.with_suffix(".json.sha256").exists())

    def test_tampered_backup_fails_without_clobbering_operator_path(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            with patch.dict(os.environ, {"OSIRIS_PAPER_EXECUTION_DB": "operator-stays-untouched"}, clear=False):
                run(source)
                os.environ["OSIRIS_PAPER_EXECUTION_DB"] = "operator-stays-untouched"
                backup = source / "local-paper-backup.sqlite3"
                backup.write_bytes(backup.read_bytes() + b"tampered")
                with self.assertRaisesRegex(RuntimeError, "source_artifacts_invalid"):
                    restore_and_verify(source, Path(directory) / "output.json")
                self.assertEqual(os.environ["OSIRIS_PAPER_EXECUTION_DB"], "operator-stays-untouched")

    def test_live_execution_environment_is_refused_before_read(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"OSIRIS_ENABLE_LIVE_EXECUTION": "1"}, clear=False):
                with self.assertRaisesRegex(RuntimeError, "live_execution_environment_refused"):
                    restore_and_verify(Path(directory) / "source", Path(directory) / "output.json")


if __name__ == "__main__":
    unittest.main()
