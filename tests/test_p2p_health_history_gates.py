"""Offline regression gates for bounded P2P health history (no remote provenance claims)."""
import unittest

from scripts.p2p_network_health_history import evaluate_health_history


def sample(t, peers, flags=None):
    return {"observed_at_ms": t, "health": {
        "status": "observed_local_health",
        "nodes": [
            {"peer_id": peer, "network_brain": {"connected_peers": count},
             "anomaly_detection": (flags or {}).get(peer, [])}
            for peer, count in peers.items()
        ],
    }}


class HealthHistoryGateTests(unittest.TestCase):
    def test_loss_and_missing_peer_are_advisory(self):
        result = evaluate_health_history([
            sample(100, {"a": 2, "b": 1}),
            sample(200, {"a": 0}),
        ])
        self.assertEqual(result["status"], "advisory_only")
        self.assertIn({"sample": 1, "peer_id": "a", "kind": "connectivity_lost"}, result["alerts"])
        self.assertIn({"sample": 1, "peer_id": "b", "kind": "node_missing"}, result["alerts"])
        self.assertFalse(result["authenticated_peers_verified"])
        self.assertFalse(result["remote_delivery_verified"])
        self.assertFalse(result["live_trading_authorized"])

    def test_rejects_duplicate_peers_and_replayed_timestamps(self):
        first = sample(100, {"a": 1})
        duplicate = sample(200, {"a": 1})
        duplicate["health"]["nodes"].append(dict(duplicate["health"]["nodes"][0]))
        self.assertEqual(evaluate_health_history([first, duplicate])["reason"], "invalid_node_metrics")
        self.assertEqual(evaluate_health_history([first, sample(100, {"a": 0})])["reason"],
                         "nonmonotonic_sample_time")

    def test_alert_flags_are_bounded_and_validated(self):
        result = evaluate_health_history([
            sample(1, {"a": 1}),
            sample(2, {"a": 1}, {"a": ["partial_bootstrap_disconnect"]}),
        ])
        self.assertIn({"sample": 1, "peer_id": "a", "kind": "partial_bootstrap_disconnect"},
                      result["alerts"])
        self.assertEqual(evaluate_health_history([
            sample(1, {"a": 1}), sample(2, {"a": 1}, {"a": ["unknown"]})
        ])["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
