import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from verify_paper_dr_artifacts import verify


class VerifyPaperDrArtifactsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        backup = b"synthetic-paper-ledger-only"
        for name in ("local-paper-backup.sqlite3", "local-paper-restored.sqlite3"):
            (self.root / name).write_bytes(backup)
        digest = hashlib.sha256(backup).hexdigest()
        self.report = {
            "schema": "osiris.local-paper-ledger-dr.v1",
            "status": "passed",
            "scope": "single-runner-temporary-sqlite-paper-ledger",
            "temporary_fixture_only": True,
            "independent_hosts_verified": False,
            "production_service_recovery_verified": False,
            "real_dr_failover_tested": False,
            "live_execution": False,
            "broker_order_submitted": False,
            "t18_release_gate_accepted": False,
            "commit_sha": "a" * 40,
            "backup_sha256": digest,
            "restored_sha256": digest,
        }
        self.write_artifacts()

    def write_artifacts(self):
        (self.root / "local-paper-recovery-report.json").write_text(json.dumps(self.report))
        lines = [
            f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
            for p in sorted(self.root.iterdir())
            if p.is_file() and p.name != "sha256-manifest.txt"
        ]
        (self.root / "sha256-manifest.txt").write_text("".join(lines))

    def test_valid_local_artifacts(self):
        self.assertEqual(verify(self.root), [])

    def test_tampered_backup_rejected(self):
        (self.root / "local-paper-backup.sqlite3").write_bytes(b"tampered")
        self.assertTrue(verify(self.root))

    def test_forged_production_claim_rejected(self):
        self.report["production_service_recovery_verified"] = True
        self.write_artifacts()
        self.assertTrue(verify(self.root))

    def test_forged_gate_acceptance_rejected(self):
        self.report["t18_release_gate_accepted"] = True
        self.write_artifacts()
        self.assertTrue(verify(self.root))

    def test_missing_manifest_rejected(self):
        (self.root / "sha256-manifest.txt").unlink()
        self.assertTrue(verify(self.root))

    def test_manifest_tamper_rejected(self):
        (self.root / "sha256-manifest.txt").write_text("fake")
        self.assertTrue(verify(self.root))


if __name__ == "__main__":
    unittest.main()
