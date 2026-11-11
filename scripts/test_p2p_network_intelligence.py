"""Deterministic network intelligence negative and tie-break tests."""
from __future__ import annotations

import unittest

from p2p_network_intelligence import advise


def peer(name, latency=30, delivery=9990, disconnects=0, duplicates=0):
    return {"peer_id": name, "latency_ms": latency, "delivery_success_bps": delivery,
            "disconnects_10m": disconnects, "duplicate_messages_10m": duplicates,
            "authenticated": True}


class NetworkIntelligenceTests(unittest.TestCase):
    def test_ranks_healthy_peers_deterministically(self):
        data = {"schema": "osiris.p2p_network_intelligence.v1",
                "peers": [peer("z", 20), peer("a", 20), peer("slow", 500)]}
        result = advise(data)
        self.assertEqual(result["status"], "advisory_only")
        self.assertEqual([p["peer_id"] for p in result["ranked_peers"]], ["a", "z", "slow"])
        self.assertFalse(result["automatic_routing_enabled"])
        self.assertFalse(result["live_trading_authorized"])

    def test_anomaly_flags_and_healthy_priority(self):
        data = {"schema": "osiris.p2p_network_intelligence.v1",
                "peers": [peer("bad", 1, 9000, 5, 100), peer("good", 90)]}
        result = advise(data)
        self.assertEqual(result["ranked_peers"][0]["peer_id"], "good")
        self.assertEqual(len(result["ranked_peers"][1]["anomalies"]), 3)
        self.assertFalse(result["network_anomaly_proven"])

    def test_duplicate_identity_rejected(self):
        data = {"schema": "osiris.p2p_network_intelligence.v1",
                "peers": [peer("a"), peer("a")]}
        self.assertEqual(advise(data)["status"], "rejected")

    def test_unauthenticated_or_impossible_metrics_rejected(self):
        data = {"schema": "osiris.p2p_network_intelligence.v1", "peers": [peer("a")]}
        data["peers"][0]["authenticated"] = False
        self.assertEqual(advise(data)["status"], "rejected")
        data["peers"][0]["authenticated"] = True
        data["peers"][0]["delivery_success_bps"] = 10001
        self.assertEqual(advise(data)["status"], "rejected")

    def test_boolean_metric_rejected(self):
        data = {"schema": "osiris.p2p_network_intelligence.v1", "peers": [peer("a")]}
        data["peers"][0]["latency_ms"] = True
        self.assertIn("invalid_metric", advise(data)["errors"])


if __name__ == "__main__":
    unittest.main()
