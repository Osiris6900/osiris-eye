"""Synthetic model evidence bundle regression tests; NEVER gate acceptance."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from record_model_research_evidence import (
    DATA_FILE, MANIFEST_FILE, REPORT_FILE, GOVERNANCE_FILE,
    make_bundle, verify_bundle,
)

COMMIT = "c" * 40


class SyntheticArchiveTests(unittest.TestCase):
    def test_archive_has_stable_research_only_lineage(self):
        with tempfile.TemporaryDirectory() as root:
            first = Path(root) / "first"
            second = Path(root) / "second"
            a = make_bundle(first, commit_sha=COMMIT)
            b = make_bundle(second, commit_sha=COMMIT)
            self.assertEqual(a, b)
            self.assertFalse(a["t11_t12_accepted"])
            self.assertFalse(a["live_trading_authorized"])
            self.assertFalse(a["vendor_data_used"])
            self.assertEqual(a["model_approval"], "PENDING")
            self.assertEqual(a["source"], "synthetic-deterministic-fixture")
            self.assertEqual(set(a["files_sha256"]), {
                DATA_FILE, REPORT_FILE, GOVERNANCE_FILE,
            })
            self.assertEqual(a["observations"], 32)
            self.assertGreater(a["folds"], 0)
            self.assertGreater(a["transaction_cost_bps_per_unit_turnover"], 0)
            self.assertTrue(verify_bundle(first, expected_commit=COMMIT)["consistent"])
            self.assertEqual((first / "sha256sum.txt").read_text().count("\n"), 4)

    def test_modified_input_is_detected_before_acceptance(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            make_bundle(directory, commit_sha=COMMIT)
            ticks = json.loads((directory / DATA_FILE).read_text())
            ticks[0]["price"] = "9999"
            (directory / DATA_FILE).write_text(json.dumps(ticks))
            with self.assertRaisesRegex(ValueError, "file_hash_mismatch"):
                verify_bundle(directory, expected_commit=COMMIT)

    def test_tampered_report_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            make_bundle(directory, commit_sha=COMMIT)
            report = json.loads((directory / REPORT_FILE).read_text())
            report["aggregate"]["total_costs_paid"] = 0.0
            (directory / REPORT_FILE).write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, "file_hash_mismatch"):
                verify_bundle(directory, expected_commit=COMMIT)

    def test_wrong_candidate_commit_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            make_bundle(directory, commit_sha=COMMIT)
            with self.assertRaisesRegex(ValueError, "contract_invalid"):
                verify_bundle(directory, expected_commit="d" * 40)

    def test_manifest_cannot_claim_accepted_gate(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            make_bundle(directory, commit_sha=COMMIT)
            manifest = json.loads((directory / MANIFEST_FILE).read_text())
            manifest["t11_t12_accepted"] = True
            (directory / MANIFEST_FILE).write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "contract_invalid"):
                verify_bundle(directory, expected_commit=COMMIT)

    def test_lineage_cannot_be_changed_even_after_rehash(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            make_bundle(directory, commit_sha=COMMIT)
            manifest = json.loads((directory / MANIFEST_FILE).read_text())
            governance = json.loads((directory / GOVERNANCE_FILE).read_text())
            governance["approvals"] = ["fictional-signoff"]
            (directory / GOVERNANCE_FILE).write_text(json.dumps(governance))
            import hashlib
            manifest["files_sha256"][GOVERNANCE_FILE] = hashlib.sha256(
                (directory / GOVERNANCE_FILE).read_bytes()
            ).hexdigest()
            (directory / MANIFEST_FILE).write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "lineage_or_safety_invalid"):
                verify_bundle(directory, expected_commit=COMMIT)

    def test_invalid_sha_refused(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, "exact_commit_sha_required"):
                make_bundle(Path(root), commit_sha="main")


if __name__ == "__main__":
    unittest.main()
