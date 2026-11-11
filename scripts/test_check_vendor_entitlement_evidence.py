"""Tests for fail-closed market-data vendor rights metadata."""
from __future__ import annotations

from datetime import date
import unittest
from check_vendor_entitlement_evidence import validate


def example():
    return {
        "schema": "osiris.vendor-entitlement-evidence.v1",
        "vendor": "EXAMPLE — not a real contract",
        "contract_reference": "SAMPLE-ONLY",
        "reviewer": "EXAMPLE-ONLY",
        "reviewed_on": "2026-11-01",
        "expires_on": "2027-11-01",
        "permitted_uses": ["internal_research", "paper_trading"],
        "instruments": ["BTC-USD"],
        "evidence_url": "https://example.invalid/sample-only",
        "evidence_sha256": "a" * 64,
        "vendor_countersignature_verified": False,
    }


class VendorEvidenceTests(unittest.TestCase):
    def test_malformed_https_urls_are_rejected(self):
        for url in ("https://[broken", "https://example.invalid:wrong/report",
                    "https://example.invalid:99999/report"):
            with self.subTest(url=url):
                data = example()
                data["evidence_url"] = url
                self.assertIn("invalid_evidence_url",
                              validate(data, as_of=date(2026, 11, 11))["errors"])

    def test_metadata_never_authorizes_live_feed(self):
        result = validate(example(), as_of=date(2026, 11, 11))
        self.assertTrue(result["metadata_valid"])
        self.assertFalse(result["vendor_rights_verified"])
        self.assertFalse(result["live_feed_authorized"])
        self.assertFalse(result["redistribution_authorized"])
        self.assertFalse(result["t09_accepted"])

    def test_invalid_or_expired_evidence_rejected(self):
        for field, value in (
            ("reviewed_on", "2027-01-01"),
            ("expires_on", "2026-10-01"),
            ("evidence_sha256", "wrong"),
            ("evidence_url", "http://example.invalid"),
            ("permitted_uses", ["paper_trading", "paper_trading"]),
            ("permitted_uses", ["all_uses"]),
            ("instruments", []),
            ("vendor", ""),
            ("vendor_countersignature_verified", "yes"),
        ):
            with self.subTest(field=field):
                data = example()
                data[field] = value
                self.assertFalse(validate(data, as_of=date(2026, 11, 11))["metadata_valid"])

    def test_claimed_countersignature_is_not_independent_verification(self):
        data = example()
        data["vendor_countersignature_verified"] = True
        result = validate(data, as_of=date(2026, 11, 11))
        self.assertTrue(result["metadata_valid"])
        self.assertFalse(result["vendor_rights_verified"])


if __name__ == "__main__":
    unittest.main()
