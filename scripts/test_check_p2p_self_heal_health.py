"""P2P bootstrap outage/recovery offline health tests."""
from __future__ import annotations

import copy
import unittest

from check_p2p_self_heal_health import evaluate


def fixture():
    before = {"status": "alive", "peer_id": "example-peer",
              "connected_peers": 0, "bootstrap_peers_connected": 0,
              "bootstrap_peers_total": 2, "uptime_seconds": 10}
    after = {**before, "connected_peers": 2,
             "bootstrap_peers_connected": 2, "uptime_seconds": 80}
    return {"before": before, "after": after,
            "outage_duration_seconds": 30, "recovery_duration_seconds": 40}


class SelfHealHealthTests(unittest.TestCase):
    def test_reported_recovery_not_proof(self):
        result = evaluate(fixture())
        self.assertEqual(result["state"], "reported_recovered")
        self.assertFalse(result["self_healing_proven"])
        self.assertFalse(result["remote_delivery_verified"])
        self.assertFalse(result["release_accepted"])

    def test_identity_change_rejected(self):
        data = fixture()
        data["after"]["peer_id"] = "different"
        self.assertIn("node_identity_changed", evaluate(data)["errors"])

    def test_missing_bootstrap_rejected(self):
        data = fixture()
        data["after"]["bootstrap_peers_connected"] = 0
        self.assertIn("bootstrap_not_recovered", evaluate(data)["errors"])

    def test_recovery_timeout_rejected(self):
        data = fixture()
        data["recovery_duration_seconds"] = 121
        self.assertIn("recovery_slo_exceeded", evaluate(data)["errors"])

    def test_inconsistent_counts_rejected(self):
        data = fixture()
        data["after"]["bootstrap_peers_connected"] = 3
        self.assertIn("after_inconsistent_counts", evaluate(data)["errors"])

    def test_no_outage_rejected(self):
        data = fixture()
        data["before"]["bootstrap_peers_connected"] = 1
        data["before"]["connected_peers"] = 1
        self.assertIn("outage_not_observed", evaluate(data)["errors"])


if __name__ == "__main__":
    unittest.main()
