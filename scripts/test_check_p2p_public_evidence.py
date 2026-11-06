"""Offline regression tests for operator-declared multi-vantage evidence."""
import importlib.util
from datetime import datetime, timezone, timedelta
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "evidence_gate", Path(__file__).with_name("check_p2p_public_evidence.py")
)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

NOW = datetime(2026, 11, 4, 3, 0, tzinfo=timezone.utc)


def record(vantage: str) -> dict:
    return {
        "timestamp_utc": NOW.isoformat(),
        "target_host": "node.example.org",
        "target_port": 4001,
        "tcp_reachable": True,
        "verification_level": "tcp_only",
        "authenticated_p2p_verified": False,
        "e2ee_verified": False,
        "relay_fallback_verified": False,
        "vantage_id": vantage,
    }


def verify(records):
    return gate.check(records, "node.example.org", 4001, 3600, NOW)


class EvidenceGateTests(unittest.TestCase):
    def test_two_distinct_vantages_pass_without_p2p_claim(self):
        result = verify([record("site-a"), record("site-b")])
        self.assertEqual(result["status"], "pass")
        self.assertFalse(result["independent_networks_attested"])
        self.assertFalse(result["e2ee_verified"])

    def test_single_or_duplicate_vantage_rejected(self):
        for records in ([record("site-a")], [record("site-a"), record("site-a")]):
            with self.assertRaises(ValueError):
                verify(records)

    def test_unreachable_or_mismatched_target_rejected(self):
        for field, value in (("tcp_reachable", False), ("target_host", "other.example.org"),
                             ("target_port", 4002), ("verification_level", "e2ee"),
                             ("e2ee_verified", True)):
            bad = record("site-b")
            bad[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify([record("site-a"), bad])

    def test_stale_future_and_naive_times_rejected(self):
        for value in ((NOW - timedelta(hours=2)).isoformat(),
                      (NOW + timedelta(minutes=2)).isoformat(),
                      "2026-11-04T03:00:00", "not-a-time"):
            bad = record("site-b")
            bad["timestamp_utc"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                verify([record("site-a"), bad])

    def test_boolean_port_not_accepted(self):
        bad = record("site-b")
        bad["target_port"] = True
        with self.assertRaises(ValueError):
            verify([record("site-a"), bad])


if __name__ == "__main__":
    unittest.main()
