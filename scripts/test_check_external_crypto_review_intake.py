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
        data["reviewer"]["conflicts_disclosed"] = True
        result = check(data)
        self.assertTrue(result["intake_valid"])
        self.assertFalse(result["t06_accepted"])
        self.assertFalse(result["independent_review_verified"])

    def test_approval_declarations_require_independence_and_disclosure(self):
        for decision in ("approved", "approved_with_remediation"):
            for field in ("independence_attested", "conflicts_disclosed"):
                with self.subTest(decision=decision, field=field):
                    data = sample()
                    data["decision"] = decision
                    data["reviewer"].update(independence_attested=True, conflicts_disclosed=True)
                    data["reviewer"][field] = False
                    result = check(data)
                    self.assertFalse(result["intake_valid"])
                    self.assertIn("approval_requires_" + field, result["failures"])

    def test_approval_declarations_require_signed_report(self):
        for decision in ("approved", "approved_with_remediation"):
            with self.subTest(decision=decision):
                data = sample()
                data["decision"] = decision
                data["reviewer"].update(independence_attested=True, conflicts_disclosed=True)
                data["artifacts"][0]["kind"] = "finding_register"
                result = check(data)
                self.assertFalse(result["intake_valid"])
                self.assertIn("approval_requires_signed_report", result["failures"])

    def test_unconditional_approval_rejects_open_critical_high_findings(self):
        for severity in ("critical", "high"):
            with self.subTest(severity=severity):
                data = sample()
                data["decision"] = "approved"
                data["reviewer"].update(independence_attested=True, conflicts_disclosed=True)
                data["findings"] = [{"id": "A-1", "severity": severity, "status": "open"}]
                result = check(data)
                self.assertFalse(result["intake_valid"])
                self.assertIn("approved_with_open_critical_high_findings", result["failures"])

    def test_conditional_review_preserves_honest_open_findings_without_acceptance(self):
        data = sample()
        data["decision"] = "approved_with_remediation"
        data["reviewer"].update(independence_attested=True, conflicts_disclosed=True)
        data["findings"] = [{"id": "A-1", "severity": "high", "status": "open"}]
        result = check(data)
        self.assertTrue(result["intake_valid"])
        self.assertFalse(result["t06_accepted"])
        self.assertFalse(result["independent_review_verified"])
        self.assertFalse(result["signed_report_cryptographically_verified"])
        self.assertFalse(result["live_trading_authorized"])

    def test_pending_and_rejected_intakes_preserve_false_attestations(self):
        for decision in ("pending", "rejected"):
            with self.subTest(decision=decision):
                data = sample()
                data["decision"] = decision
                self.assertTrue(check(data)["intake_valid"])


if __name__ == "__main__":
    unittest.main()
