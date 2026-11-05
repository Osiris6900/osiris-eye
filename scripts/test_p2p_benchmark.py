#!/usr/bin/env python3
"""Offline tests for the simulated P2P benchmark harness (issue #86)."""
import json
import unittest

from p2p_benchmark import main, run_benchmark, simulate_gossip


class SimulateGossipTests(unittest.TestCase):
    def test_full_delivery_without_churn(self):
        result = simulate_gossip(peers=10, fanout=3, messages=5, seed=7)
        self.assertEqual(result["final_delivery_ratio"], 1.0)
        self.assertGreater(result["total_transmissions"], 0)
        self.assertGreaterEqual(result["max_hops"], 1)
        self.assertLessEqual(result["max_hops"], result["rounds_capped"])

    def test_send_queue_bounded_by_fanout(self):
        for fanout in (1, 4, 16):
            result = simulate_gossip(peers=100, fanout=fanout, messages=3, seed=11)
            self.assertTrue(result["send_queue_bounded"])
            self.assertLessEqual(result["max_per_node_send_queue"], fanout)

    def test_deterministic_given_seed(self):
        first = simulate_gossip(peers=50, fanout=4, messages=5, seed=99)
        second = simulate_gossip(peers=50, fanout=4, messages=5, seed=99)
        for key in ("total_transmissions", "duplicates_suppressed",
                    "mean_hops", "final_delivery_ratio"):
            self.assertEqual(first[key], second[key])

    def test_churn_reduces_or_preserves_delivery_within_bounds(self):
        result = simulate_gossip(peers=60, fanout=4, messages=8, churn_rate=0.3, seed=5)
        self.assertGreaterEqual(result["final_delivery_ratio"], 0.0)
        self.assertLessEqual(result["final_delivery_ratio"], 1.0)

    def test_fail_closed_bounds(self):
        with self.assertRaises(ValueError):
            simulate_gossip(peers=1, fanout=3, messages=5)
        with self.assertRaises(ValueError):
            simulate_gossip(peers=10_000, fanout=3, messages=5)
        with self.assertRaises(ValueError):
            simulate_gossip(peers=10, fanout=0, messages=5)
        with self.assertRaises(ValueError):
            simulate_gossip(peers=10, fanout=3, messages=0)
        with self.assertRaises(ValueError):
            simulate_gossip(peers=10, fanout=3, messages=5, churn_rate=1.5)


class RunBenchmarkTests(unittest.TestCase):
    def test_resource_metrics_present(self):
        result = run_benchmark(peers=10, fanout=3, messages=3, seed=3)
        for key in ("wall_seconds", "cpu_seconds", "peak_memory_bytes",
                    "transmissions_per_second"):
            self.assertIn(key, result)
        self.assertGreaterEqual(result["wall_seconds"], 0.0)
        self.assertGreater(result["peak_memory_bytes"], 0)


class CliTests(unittest.TestCase):
    def test_json_report_schema(self):
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exit_code = main(["--peers", "10", "--fanout", "3", "--messages", "3"])
        self.assertEqual(exit_code, 0)
        report = json.loads(buffer.getvalue())
        self.assertEqual(report["result_class"], "simulated_process_local")
        self.assertFalse(report["multi_host_evidence"])
        self.assertFalse(report["production_readiness_evidence"])
        self.assertEqual(len(report["runs"]), 1)
        self.assertIn("environment", report)

    def test_invalid_config_exits_2(self):
        import io
        from contextlib import redirect_stderr

        buffer = io.StringIO()
        with redirect_stderr(buffer):
            exit_code = main(["--peers", "10", "--fanout", "0"])
        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
