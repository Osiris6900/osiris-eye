#!/usr/bin/env python3
"""Offline tests for the P2P fault-injection and recovery matrix (issue #91)."""
import io
import json
import unittest
from contextlib import redirect_stdout

from p2p_fault_injection import (
    MAX_BACKOFF_SECONDS,
    MAX_RECONNECT_ATTEMPTS,
    SCENARIOS,
    backoff_schedule,
    main,
    run_matrix,
)


class MatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = {item["scenario"]: item for item in run_matrix()}

    def test_all_scenarios_pass(self):
        for name, item in self.results.items():
            self.assertTrue(item["passed"], f"scenario failed: {name}: {item}")

    def test_matrix_covers_required_faults(self):
        required = {
            "abrupt_disconnect",
            "bootstrap_outage_recovery",
            "relay_failover",
            "reconnect_backoff",
            "partial_partition",
            "duplicate_delivery",
            "stale_peer_eviction",
            "revoked_peer_fail_closed",
        }
        self.assertEqual(set(self.results), required)
        self.assertEqual(len(SCENARIOS), len(required))

    def test_backoff_is_bounded_and_capped(self):
        details = self.results["reconnect_backoff"]["details"]
        self.assertEqual(details["failed_schedule"], [1.0, 2.0, 4.0, 8.0, 8.0])
        self.assertEqual(details["max_attempts"], MAX_RECONNECT_ATTEMPTS)
        self.assertEqual(details["backoff_cap_seconds"], MAX_BACKOFF_SECONDS)
        self.assertEqual(details["recovery_attempts"], 1)
        schedule = backoff_schedule(MAX_RECONNECT_ATTEMPTS)
        self.assertTrue(all(delay <= MAX_BACKOFF_SECONDS for delay in schedule))

    def test_partition_does_not_leak_and_heals(self):
        details = self.results["partial_partition"]["details"]
        self.assertFalse(details["leaked_during_partition"])
        self.assertTrue(details["healed_after_rejoin"])
        self.assertGreaterEqual(details["synced_messages"], 2)

    def test_duplicate_delivery_stores_single_copy(self):
        details = self.results["duplicate_delivery"]["details"]
        self.assertEqual(details["stored_copies"], 1)
        self.assertEqual(details["duplicates_suppressed"], 1)

    def test_revoked_peer_fails_closed(self):
        details = self.results["revoked_peer_fail_closed"]["details"]
        self.assertFalse(details["reconnect_accepted"])
        self.assertEqual(details["deliveries_from_revoked"], 0)
        self.assertEqual(details["rejected_connections"], 1)

    def test_stale_peer_evicted_before_reuse(self):
        details = self.results["stale_peer_eviction"]["details"]
        self.assertIn("b", details["evicted"])
        self.assertEqual(details["delivered_after_eviction"], 1)


class CliTests(unittest.TestCase):
    def test_cli_exit_zero_and_schema(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exit_code = main()
        self.assertEqual(exit_code, 0)
        report = json.loads(buffer.getvalue())
        self.assertEqual(report["result_class"], "simulated_process_local")
        self.assertFalse(report["multi_host_evidence"])
        self.assertTrue(report["all_passed"])
        self.assertIn("bounds", report)
        self.assertEqual(len(report["scenarios"]), 8)


if __name__ == "__main__":
    unittest.main()
