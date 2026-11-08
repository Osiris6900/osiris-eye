import unittest
from check_release_manifest import validate

class ReleaseManifestTests(unittest.TestCase):
    def record(self):
        return {"commit_sha":"a"*40,"clean_checkout":True,"ci_passed":True,"security_review_signed":True,"paper_reconciliation_passed":True,"kill_switch_test_passed":True,"operator_approval":True,"p2p_evidence_sha256":"b"*64,"artifact_sha256":"c"*64,"backup_restore_test_passed":True,"live_trading_enabled":False,
                "staging_smoke_passed":True,"sbom_verified":True,"provenance_verified":True,
                "metrics_alerts_verified":True,"restore_drill_passed":True,
                "sbom_sha256":"d"*64,"provenance_sha256":"e"*64,
                "staging_report_sha256":"f"*64,"restore_report_sha256":"1"*64}
    def test_valid_shape(self): self.assertEqual(validate(self.record()), [])
    def test_missing_deployment_evidence_denied(self):
        for field in ("staging_smoke_passed", "sbom_verified", "provenance_verified",
                      "metrics_alerts_verified", "restore_drill_passed"):
            record = self.record()
            record[field] = False
            self.assertTrue(validate(record), field)

    def test_missing_evidence_digest_denied(self):
        for field in ("sbom_sha256", "provenance_sha256", "staging_report_sha256",
                      "restore_report_sha256"):
            record = self.record()
            record[field] = "invalid"
            self.assertTrue(validate(record), field)

    def test_missing_approval(self):
        r=self.record(); r["operator_approval"]=False; self.assertTrue(validate(r))
    def test_live_enabled_denied(self):
        r=self.record(); r["live_trading_enabled"]=True; self.assertTrue(validate(r))
    def test_missing_artifact_hash(self):
        r=self.record(); r["artifact_sha256"]=""; self.assertTrue(validate(r))
    def test_missing_backup_restore_evidence_denied(self):
        r=self.record(); r["backup_restore_test_passed"]=False; self.assertTrue(validate(r))
if __name__=="__main__": unittest.main()
