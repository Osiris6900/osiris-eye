"""Five-capability P2P advisory tests."""
from __future__ import annotations

import unittest
from p2p_intelligence_five import analyze


def fixture():
    def sample(t, latency, delivery, disconnects=0, duplicates=0):
        return {"at_ms": t, "latency_ms": latency, "delivery_bps": delivery,
                "disconnects": disconnects, "duplicates": duplicates}
    return {"schema": "osiris.p2p_intelligence.v1", "nodes": [
        {"peer_id": "good", "authenticated": True, "samples": [
            sample(1, 50, 9990), sample(2, 40, 9990)]},
        {"peer_id": "degrading", "authenticated": True, "samples": [
            sample(1, 100, 9990), sample(2, 400, 9000, 5, 100)]},
    ]}


class FiveCapabilitiesTests(unittest.TestCase):
    def test_all_five_outputs_and_no_network_mutation(self):
        result = analyze(fixture())
        self.assertEqual(result["status"], "advisory_only")
        self.assertEqual(result["smart_routing"][0]["peer_id"], "good")
        self.assertEqual(len(result["network_brain"]), 2)
        self.assertEqual(result["anomaly_detection"][0]["peer_id"], "degrading")
        self.assertTrue(result["predictive_failure"][0]["trend_flags"])
        self.assertFalse(result["predictive_failure"][0]["prediction_validated"])
        self.assertTrue(result["optimization"][0]["requires_operator_approval"])
        self.assertFalse(result["automatic_changes_applied"])
        self.assertFalse(result["live_trading_authorized"])

    def test_unauthenticated_node_rejected(self):
        data = fixture()
        data["nodes"][0]["authenticated"] = False
        self.assertEqual(analyze(data)["status"], "rejected")

    def test_non_monotonic_time_rejected(self):
        data = fixture()
        data["nodes"][0]["samples"][1]["at_ms"] = 1
        self.assertEqual(analyze(data)["errors"], ["invalid_sample_range_or_order"])

    def test_duplicate_node_rejected(self):
        data = fixture()
        data["nodes"][1]["peer_id"] = "good"
        self.assertEqual(analyze(data)["status"], "rejected")

    def test_boolean_metric_rejected(self):
        data = fixture()
        data["nodes"][0]["samples"][0]["latency_ms"] = True
        self.assertEqual(analyze(data)["errors"], ["invalid_sample_metric"])


if __name__ == "__main__":
    unittest.main()
