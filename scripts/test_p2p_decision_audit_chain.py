"""Tamper-evident P2P decision audit tests."""
import unittest
from p2p_decision_audit_chain import append, verify


def event(identity="decision-1"):
    return {"decision_id": identity, "peer_id": "peer-a",
            "observed_at_ms": 1000, "decision": "route_advisory",
            "evidence_sha256": "a" * 64}


class AuditTests(unittest.TestCase):
    def test_append_and_verify_two_links(self):
        chain = append([], event())
        chain = append(chain, event("decision-2"))
        self.assertEqual(verify(chain)["status"], "valid")
        self.assertEqual(verify(chain)["entries"], 2)
        self.assertFalse(verify(chain)["signed"])
    def test_tampered_record_detected(self):
        chain = append([], event())
        chain[0]["event"]["peer_id"] = "attacker"
        self.assertEqual(verify(chain)["reason"], "entry_hash_mismatch")
    def test_truncation_detected_with_trusted_tip(self):
        chain = append(append([], event()), event("decision-2"))
        tip = verify(chain)["tip_sha256"]
        self.assertEqual(verify(chain[:1], tip)["reason"], "trusted_tip_mismatch")
    def test_duplicate_rejected(self):
        chain = append([], event())
        with self.assertRaises(ValueError):
            append(chain, event())
    def test_invalid_evidence_hash_rejected(self):
        bad = event()
        bad["evidence_sha256"] = "invalid"
        with self.assertRaises(ValueError):
            append([], bad)
    def test_invalid_index_rejected(self):
        chain = append([], event())
        chain[0]["index"] = True
        self.assertEqual(verify(chain)["reason"], "chain_link_mismatch")


if __name__ == "__main__":
    unittest.main()
