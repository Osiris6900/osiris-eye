import unittest
from check_p2p_release_evidence import check

class ReleaseEvidenceTests(unittest.TestCase):
    def record(self):
        return {"commit_sha":"a"*40,"clean_checkout":True,"topology":"three_independent_hosts","hosts":[{"host_id":str(i),"binary_sha256":"b"*64} for i in range(3)],"payload_sha256":"c"*64,"recipient_digest_matched":True,"nonrecipient_discarded":True,"workflow_url":"https://github.com/example/repo/actions/runs/1"}
    def test_valid_metadata(self):
        self.assertEqual(check(self.record()), [])
    def test_missing_binary_hash_fails(self):
        r=self.record(); del r["hosts"][0]["binary_sha256"]
        self.assertTrue(check(r))
    def test_same_host_fails(self):
        r=self.record(); r["hosts"][1]["host_id"]="0"
        self.assertTrue(check(r))
    def test_recipient_leak_fails(self):
        r=self.record(); r["nonrecipient_discarded"]=False
        self.assertTrue(check(r))
if __name__=="__main__": unittest.main()
