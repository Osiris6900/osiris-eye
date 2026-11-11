"""Five-part P2P intelligence regression tests."""
import unittest
from p2p_intelligence_five import evaluate

def peer(name="a"):
    return {"peer_id": name, "authenticated": True, "samples": 25,
            "latency_ms": 20, "delivery_bps": 9990,
            "previous_delivery_bps": 9990, "disconnects_10m": 0,
            "duplicates_10m": 0, "cpu_percent": 10, "queue_depth": 0}

def run(peers):
    return evaluate({"schema": "osiris.p2p.intelligence.v1", "peers": peers})

class FiveTests(unittest.TestCase):
    def test_all_five_present_and_no_mutation(self):
        r = run([peer()])
        self.assertEqual(r["status"], "advisory_only")
        self.assertFalse(r["network_mutated"])
        self.assertFalse(r["live_trading_authorized"])
        self.assertEqual(set(r["ranked_peers"][0]) - {"peer_id"},
            {"network_brain", "smart_routing", "anomaly_detection", "predictive_failure", "autonomous_optimization"})
    def test_anomalies_and_warning(self):
        p = peer()
        p.update(latency_ms=2500, delivery_bps=8000, disconnects_10m=5,
                 duplicates_10m=100, cpu_percent=95, queue_depth=1500)
        r = run([p])["ranked_peers"][0]
        self.assertEqual(len(r["anomaly_detection"]), 4)
        self.assertTrue(r["predictive_failure"]["early_warning"])
        self.assertFalse(r["autonomous_optimization"]["applied"])
        self.assertEqual(len(r["autonomous_optimization"]["suggestions"]), 3)
    def test_invalid_peer_rejected(self):
        self.assertEqual(run([peer("x"), peer("x")])["status"], "rejected")
        p = peer()
        p["authenticated"] = False
        self.assertEqual(run([p])["status"], "rejected")
        p = peer()
        p["samples"] = 2
        self.assertEqual(run([p])["status"], "rejected")
    def test_deterministic_ranking(self):
        a, b = peer("z"), peer("a")
        self.assertEqual([x["peer_id"] for x in run([a,b])["ranked_peers"]], ["a","z"])

if __name__ == "__main__":
    unittest.main()
