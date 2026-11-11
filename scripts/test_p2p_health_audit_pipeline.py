"""Integration contract for real Rust health -> decision audit."""
import unittest
from p2p_health_audit_pipeline import run_pipeline
from p2p_decision_audit_chain import verify


def health(peer="peer-1", connected=1, bootstrap=1):
    return {"status": "alive", "peer_id": peer, "uptime_seconds": 10,
            "connected_peers": connected, "bootstrap_peers_connected": bootstrap,
            "bootstrap_peers_total": 1}


class PipelineTests(unittest.TestCase):
    def test_healthy_creates_observe_evidence(self):
        r = run_pipeline([health()], 1000)
        self.assertEqual(r["status"], "advisory_only")
        self.assertEqual(r["audit_chain"][0]["event"]["decision"], "observe")
        self.assertEqual(verify(r["audit_chain"])["status"], "valid")
        self.assertFalse(r["network_mutated"])
        self.assertFalse(r["durable_audit_storage"])
    def test_isolation_creates_investigate_evidence(self):
        r = run_pipeline([health(connected=0, bootstrap=0)], 1000)
        self.assertEqual(r["audit_chain"][0]["event"]["decision"], "investigate")
        self.assertIn("isolated_node", r["health"]["nodes"][0]["anomaly_detection"])
    def test_order_deterministic(self):
        r = run_pipeline([health("z"), health("a")], 1000)
        self.assertEqual([e["event"]["peer_id"] for e in r["audit_chain"]], ["a", "z"])
    def test_invalid_health_fails_closed(self):
        r = run_pipeline([health("same"), health("same")], 1000)
        self.assertEqual(r["status"], "rejected")
    def test_timestamp_rejects_bool(self):
        self.assertEqual(run_pipeline([health()], True)["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
