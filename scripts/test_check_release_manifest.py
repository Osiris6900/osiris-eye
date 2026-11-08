import unittest
from check_release_manifest import validate

class ReleaseManifestTests(unittest.TestCase):
    def record(self):
        return {"commit_sha":"a"*40,"clean_checkout":True,"ci_passed":True,"security_review_signed":True,"paper_reconciliation_passed":True,"kill_switch_test_passed":True,"operator_approval":True,"p2p_evidence_sha256":"b"*64,"artifact_sha256":"c"*64,"backup_restore_test_passed":True,"live_trading_enabled":False}
    def test_valid_shape(self): self.assertEqual(validate(self.record()), [])
    def test_missing_approval(self):
        r=self.record(); r["operator_approval"]=False; self.assertTrue(validate(r))
    def test_live_enabled_denied(self):
        r=self.record(); r["live_trading_enabled"]=True; self.assertTrue(validate(r))
    def test_missing_artifact_hash(self):
        r=self.record(); r["artifact_sha256"]=""; self.assertTrue(validate(r))
    def test_missing_backup_restore_evidence_denied(self):
        r=self.record(); r["backup_restore_test_passed"]=False; self.assertTrue(validate(r))
if __name__=="__main__": unittest.main()
