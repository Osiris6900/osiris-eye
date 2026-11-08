#!/usr/bin/env python3
"""Offline negative tests for the P2P release manifest gate."""
import unittest
from validate_p2p_release_evidence import REQUIRED, validate

class ManifestTests(unittest.TestCase):
    def valid(self):
        d = {"commit": "a"*40, "binary_sha256": "b"*64, "live_execution_enabled": False}
        for key in REQUIRED[2:]:
            d[key] = {"passed": True, "artifact": "evidence/" + key + ".json", "sha256": "c"*64}
        return d
    def test_valid_structure(self):
        self.assertEqual(validate(self.valid()), [])
    def test_missing_evidence_blocks(self):
        d = self.valid()
        del d["three_host_e2ee"]
        self.assertTrue(validate(d))
    def test_live_execution_blocks(self):
        d = self.valid()
        d["live_execution_enabled"] = True
        self.assertTrue(validate(d))
    def test_failed_review_blocks(self):
        d = self.valid()
        d["security_review"]["passed"] = False
        self.assertTrue(validate(d))
    def test_missing_hash_blocks(self):
        d = self.valid()
        d["hundred_peer_soak"]["sha256"] = ""
        self.assertTrue(validate(d))

if __name__ == "__main__":
    unittest.main()
