import unittest
from p2p_health_intelligence_bridge import evaluate_snapshots
from p2p_network_health_history import evaluate_health_history


def health(peer, connected):
    return evaluate_snapshots([{"status": "alive", "peer_id": peer,
        "uptime_seconds": 10, "connected_peers": connected,
        "bootstrap_peers_connected": min(connected, 1),
        "bootstrap_peers_total": 1}])


class NetworkHistoryTests(unittest.TestCase):
    def test_connectivity_loss(self):
        result = evaluate_health_history([
            {"observed_at_ms": 1, "health": health("peer-a", 2)},
            {"observed_at_ms": 2, "health": health("peer-a", 0)}])
        self.assertEqual(result["status"], "advisory_only")
        self.assertIn("connectivity_lost", [a["kind"] for a in result["alerts"]])
        self.assertFalse(result["live_trading_authorized"])

    def test_node_disappearance(self):
        result = evaluate_health_history([
            {"observed_at_ms": 1, "health": health("peer-a", 1)},
            {"observed_at_ms": 2, "health": health("peer-b", 1)}])
        self.assertIn("node_missing", [a["kind"] for a in result["alerts"]])

    def test_rejects_reordered_samples(self):
        samples = [{"observed_at_ms": 2, "health": health("a", 1)},
                   {"observed_at_ms": 1, "health": health("a", 1)}]
        self.assertEqual(evaluate_health_history(samples)["status"], "rejected")

    def test_rejects_untrusted_health(self):
        samples = [{"observed_at_ms": 1, "health": {}},
                   {"observed_at_ms": 2, "health": health("a", 1)}]
        self.assertEqual(evaluate_health_history(samples)["status"], "rejected")

    def test_rejects_duplicate_node(self):
        duplicate = health("a", 1)
        duplicate["nodes"].append(dict(duplicate["nodes"][0]))
        result = evaluate_health_history([
            {"observed_at_ms": 1, "health": health("a", 1)},
            {"observed_at_ms": 2, "health": duplicate}])
        self.assertEqual(result["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
