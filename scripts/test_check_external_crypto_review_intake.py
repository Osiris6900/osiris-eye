"""Offline negative tests for T06 independent security review intake."""
from __future__ import annotations

import copy
import unittest
from check_external_crypto_review_intake import check


def sample():
    return {
        "schema": "osiris.external_crypto_review_intake.v1",
        "candidate_commit": "a" * 40,
        "reviewer": {
            "organization": "EXAMPLE ONLY — not commissioned",
            "lead": "EXAMPLE ONLY",
            "independence_attested": False,
            "conflicts_disclosed": False,
        },
        "scope": {
            "p2p_e2ee": True,
            "identity_key_lifecycle": True,
            "relay_bootstrap": True,
            "replay_protection": True,
            "threat_model": True,
        },
        "artifacts": [
            {"url": "https://example.invalid/not-a-real-audit.pdf",
             "sha256": "b" * 64, "kind": "signed_report"}
        ],
        "findings": [],
        "decision": "pending",
        "paper_only": True,
    }


class AuditIntakeTests(unittest.TestCase):
    def test_unhashable_decision_is_rejected(self):
        for decision in ([], {}):
            data = sample()
            data["decision"] = decision
            self.assertIn("invalid_decision", check(data)["failures"])

    def test_malformed_https_urls_are_rejected(self):
        for url in ("https://[broken", "https://example.invalid:wrong/report",
                    "https://example.invalid:99999/report"):
            with self.subTest(url=url):
                data = sample()
                data["artifacts"][0]["url"] = url
                self.assertIn("invalid_artifact_url", check(data)["failures"])

    def test_valid_shape_never_grants_signoff(self):
        result = check(sample())
        self.assertTrue(result["intake_valid"])
        self.assertFalse(result["independent_review_verified"])
        self.assertFalse(result["signed_report_cryptographically_verified"])
        self.assertFalse(result["t06_accepted"])
        self.assertFalse(result["live_trading_authorized"])

    def test_malformed_or_forged_inputs_fail(self):
        for field, value in (
            ("candidate_commit", "short"),
            ("decision", "automatically_certified"),
            ("paper_only", False),
            ("artifacts", []),
            ("scope", {}),
            ("reviewer", {}),
            ("findings", [{"id": "A", "severity": "critical", "status": "closed"}]),
        ):
            with self.subTest(field=field):
                data = sample()
                data[field] = value
                self.assertFalse(check(data)["intake_valid"])

    def test_missing_scope_and_unsafe_urls_fail(self):
        data = sample()
        data["scope"]["replay_protection"] = False
        self.assertFalse(check(data)["intake_valid"])
        data = sample()
        data["artifacts"][0]["url"] = "http://example.invalid"
        self.assertFalse(check(data)["intake_valid"])
        data = sample()
        data["artifacts"][0]["sha256"] = "bad"
        self.assertFalse(check(data)["intake_valid"])

    def test_approved_claim_still_cannot_certify_independence(self):
        data = sample()
        data["decision"] = "approved"
        data["reviewer"]["independence_attested"] = True
        result = check(data)
        self.assertTrue(result["intake_valid"])
        self.assertFalse(result["t06_accepted"])
        self.assertFalse(result["independent_review_verified"])


if __name__ == "__main__":
    unittest.main()
