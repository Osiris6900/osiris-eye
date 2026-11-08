import unittest
from check_release_candidate_binding import check_candidate_binding

SHA = "a" * 40
GOOD = {"candidate_commit": SHA, "host_fingerprints": ["host-1", "host-2"], "live_execution_enabled": False}

class ReleaseBindingTests(unittest.TestCase):
    def test_valid_binding_only_eligible_for_review(self):
        self.assertTrue(check_candidate_binding(GOOD, SHA)["eligible_for_further_review"])

    def test_wrong_commit_blocks(self):
        self.assertFalse(check_candidate_binding({**GOOD, "candidate_commit": "b" * 40}, SHA)["eligible_for_further_review"])

    def test_same_host_blocks(self):
        self.assertFalse(check_candidate_binding({**GOOD, "host_fingerprints": ["same", "same"]}, SHA)["eligible_for_further_review"])

if __name__ == "__main__":
    unittest.main()
