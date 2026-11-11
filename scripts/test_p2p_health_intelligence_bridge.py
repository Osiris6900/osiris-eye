"""Local Rust health bridge contract and fail-closed tests."""
import unittest
from p2p_health_intelligence_bridge import evaluate_snapshots, read_loopback_health


def node(name="peer-a", connected=2, bootstrap=1, total=2):
    return {"status": "alive", "peer_id": name, "uptime_seconds": 100,
            "connected_peers": connected, "bootstrap_peers_connected": bootstrap,
            "bootstrap_peers_total": total}


class HealthBridgeTests(unittest.TestCase):
    def test_actual_health_contract_supported(self):
        r = evaluate_snapshots([node()])
        self.assertEqual(r["status"], "observed_local_health")
        self.assertFalse(r["network_mutated"])
        self.assertFalse(r["remote_delivery_verified"])
        self.assertFalse(r["nodes"][0]["smart_routing"]["ranking_available"])
    def test_isolation_flag(self):
        r = evaluate_snapshots([node(connected=0, bootstrap=0)])
        self.assertIn("isolated_node", r["nodes"][0]["anomaly_detection"])
    def test_duplicate_identity_rejected(self):
        self.assertEqual(evaluate_snapshots([node(), node()])["status"], "rejected")
    def test_impossible_counters_rejected(self):
        self.assertEqual(evaluate_snapshots([node(connected=1, bootstrap=2)])["status"], "rejected")
    def test_boolean_counters_rejected(self):
        n = node()
        n["uptime_seconds"] = True
        self.assertEqual(evaluate_snapshots([n])["status"], "rejected")
    def test_remote_endpoint_denied(self):
        for url in ("http://example.com:9090/health", "http://192.0.2.1:9090/health",
                    "https://127.0.0.1:9090/health", "http://127.0.0.1:9090/admin"):
            with self.assertRaises(ValueError):
                read_loopback_health(url)


if __name__ == "__main__":
    unittest.main()
