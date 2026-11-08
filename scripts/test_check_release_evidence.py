"""Offline unit tests for the release evidence preflight."""
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("release_gate", Path(__file__).with_name("check_release_evidence.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

SHA = "a" * 40
EVIDENCE = {"url": "https://example.org/artifact.json", "sha256": "b" * 64}


def manifest():
    return {"candidate_commit": SHA, "gates": {
        gate: {"status": "passed", "candidate_commit": SHA, "evidence": [dict(EVIDENCE)]}
        for gate in module.GATES
    }}


class ReleaseEvidenceTests(unittest.TestCase):
    def test_complete_manifest(self):
        result = module.evaluate(manifest())
        self.assertTrue(result["ready"])
        self.assertEqual(len(result["verified_gates"]), 20)

    def test_missing_gate_fails_closed(self):
        data = manifest()
        del data["gates"]["independent_crypto_audit"]
        self.assertFalse(module.evaluate(data)["ready"])

    def test_wrong_candidate_fails(self):
        data = manifest()
        data["gates"]["p2p_outage_load"]["candidate_commit"] = "c" * 40
        self.assertFalse(module.evaluate(data)["ready"])

    def test_missing_digest_fails(self):
        data = manifest()
        del data["gates"]["authorized_market_data"]["evidence"][0]["sha256"]
        self.assertFalse(module.evaluate(data)["ready"])

    def test_independent_review_fails_when_missing(self):
        data = manifest()
        data["gates"]["independent_crypto_audit"]["independent_review_required"] = True
        self.assertFalse(module.evaluate(data)["ready"])

    def test_bad_payload_fails(self):
        self.assertFalse(module.evaluate([])["ready"])


if __name__ == "__main__":
    unittest.main()
