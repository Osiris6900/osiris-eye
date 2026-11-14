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
        gate: {"status": "passed", "candidate_commit": SHA, "evidence": [dict(EVIDENCE)],
               **({"independent_reviewed": True} if gate in module.INDEPENDENT_REVIEW_GATES else {})}
        for gate in module.GATES
    }}


class ReleaseEvidenceTests(unittest.TestCase):
    def test_complete_manifest(self):
        result = module.evaluate(manifest())
        self.assertTrue(result["ready"])
        self.assertEqual(len(result["verified_gates"]), 20)
        self.assertFalse(result["independent_review_verified"])
        self.assertFalse(result["release_accepted"])
        self.assertFalse(result["live_trading_authorized"])

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
        data["gates"]["dependency_security"]["independent_review_required"] = True
        self.assertFalse(module.evaluate(data)["ready"])

    def test_mandatory_reviews_cannot_be_disabled_or_omitted(self):
        for gate in ("independent_crypto_audit", "feed_entitlements", "model_lifecycle",
                     "strategy_oos_validation", "portfolio_risk", "compliance_signoff"):
            for requirement in (None, False):
                with self.subTest(gate=gate, requirement=requirement):
                    data = manifest()
                    item = data["gates"][gate]
                    del item["independent_reviewed"]
                    if requirement is not None:
                        item["independent_review_required"] = requirement
                    result = module.evaluate(data)
                    self.assertFalse(result["ready"])
                    self.assertNotIn(gate, result["verified_gates"])
                    self.assertIn(gate + ": independent review missing", result["errors"])

    def test_mandatory_review_requires_literal_true(self):
        for value in (False, None, 1, "true", [], {}):
            with self.subTest(value=value):
                data = manifest()
                data["gates"]["independent_crypto_audit"]["independent_reviewed"] = value
                self.assertFalse(module.evaluate(data)["ready"])

    def test_review_requirement_rejects_non_boolean_values(self):
        for value in (None, 0, "false", [], {}):
            with self.subTest(value=value):
                data = manifest()
                data["gates"]["dependency_security"]["independent_review_required"] = value
                self.assertFalse(module.evaluate(data)["ready"])

    def test_malformed_gate_names_fail_closed(self):
        data = manifest()
        data["gates"][1] = {}
        data["gates"][None] = {}
        self.assertFalse(module.evaluate(data)["ready"])

    def test_bad_payload_fails(self):
        for data in ([], None, {"gates": []}):
            with self.subTest(data=data):
                result = module.evaluate(data)
                self.assertFalse(result["ready"])
                self.assertFalse(result["release_accepted"])
                self.assertFalse(result["live_trading_authorized"])


if __name__ == "__main__":
    unittest.main()
