"""Offline fail-closed checks for operator-attested outage/load evidence."""
import copy
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
spec = importlib.util.spec_from_file_location(
    "load_acceptance", SCRIPT_DIR / "check_p2p_load_acceptance.py"
)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
POLICY = json.loads(gate.POLICY_PATH.read_text(encoding="utf-8"))


def evidence():
    return {
        "schema": "osiris.p2p_load_acceptance.v1",
        "measurement_class": "operator_attested_real_multi_host",
        "commit_sha": "1" * 40,
        "clean_worktree": True,
        "workflow_url": "https://github.com/Osiris6900/osiris-eye/actions/runs/123",
        "attempt": 1,
        "operator": "release-operator",
        "started_at": "2026-11-07T00:00:00Z",
        "ended_at": "2026-11-07T00:15:00Z",
        "workload": {"peers": 100, "messages": 10000, "fanout": 8, "duration_seconds": 900},
        "delivery": {"sent": 10000, "delivered": 9901, "lost": 0, "rejected": 99, "pending": 0},
        "latency_ms": {"count": 9901, "p50": 100, "p95": 1000, "p99": 2000, "max": 2500},
        "hosts": [
            {"host_id": "host-a", "binary_sha256": "a" * 64, "rss_peak_bytes": 536870912,
             "cpu_percent": 80.0, "disk_growth_bytes": 67108864},
            {"host_id": "host-b", "binary_sha256": "b" * 64, "rss_peak_bytes": 400000000,
             "cpu_percent": 70.0, "disk_growth_bytes": 1000},
        ],
        "outage": {
            "kind": "relay_and_bootstrap",
            "outage_start_ms": 1000,
            "samples": [
                {"elapsed_ms": 0, "connected": True, "delivered": True},
                {"elapsed_ms": 1000, "connected": False, "delivered": False},
                {"elapsed_ms": 11000, "connected": True, "delivered": True},
            ],
        },
    }


def check(data):
    return gate.check(data, copy.deepcopy(POLICY))


class LoadAcceptanceTests(unittest.TestCase):
    def test_complete_evidence_passes_at_exact_limits_without_overclaiming(self):
        result = check(evidence())
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["sampled_recovery_ms"], 10000)
        self.assertEqual(result["operator_declared_host_count"], 2)
        self.assertFalse(result["independent_hosts_verified"])
        self.assertFalse(result["continuous_availability_verified"])
        self.assertFalse(result["production_readiness_evidence"])
        self.assertEqual(result["capacity_claim"], "declared_workload_only")
        self.assertEqual(len(result["policy_sha256"]), 64)

    def test_policy_declares_numerical_limits_before_measurement(self):
        self.assertEqual(POLICY["minimum_peers"], 100)
        self.assertEqual(POLICY["minimum_messages"], 10000)
        self.assertEqual(POLICY["minimum_duration_seconds"], 900)
        self.assertEqual(POLICY["maximum_recovery_ms"], 30000)
        self.assertEqual(POLICY["maximum_latency_p95_ms"], 1000)
        self.assertEqual(POLICY["maximum_latency_p99_ms"], 2000)
        self.assertEqual(POLICY["maximum_lost_messages"], 0)
        self.assertEqual(POLICY["maximum_pending_messages"], 0)

    def test_every_numerical_threshold_fails_closed(self):
        cases = []
        bad = evidence()
        bad["workload"]["peers"] = 99
        cases.append((bad, "minimum_peers"))
        bad = evidence()
        bad["workload"]["messages"] = 9999
        bad["delivery"]["sent"] = 9999
        bad["delivery"]["rejected"] = 98
        cases.append((bad, "minimum_messages"))
        bad = evidence()
        bad["workload"]["duration_seconds"] = 899
        bad["ended_at"] = "2026-11-07T00:14:59Z"
        cases.append((bad, "minimum_duration_seconds"))
        bad = evidence()
        bad["latency_ms"]["p95"] = 1001
        cases.append((bad, "maximum_latency_p95_ms"))
        bad = evidence()
        bad["latency_ms"]["p99"] = 2001
        cases.append((bad, "maximum_latency_p99_ms"))
        bad = evidence()
        bad["delivery"]["lost"] = 1
        bad["delivery"]["delivered"] = 9900
        bad["latency_ms"]["count"] = 9900
        cases.append((bad, "maximum_lost_messages"))
        bad = evidence()
        bad["delivery"]["rejected"] = 101
        bad["delivery"]["delivered"] = 9899
        bad["latency_ms"]["count"] = 9899
        cases.append((bad, "maximum_rejected_messages"))
        bad = evidence()
        bad["delivery"]["pending"] = 1
        bad["delivery"]["delivered"] = 9900
        bad["latency_ms"]["count"] = 9900
        cases.append((bad, "maximum_pending_messages"))
        for field in ("rss_peak_bytes", "disk_growth_bytes"):
            bad = evidence()
            bad["hosts"][0][field] *= 2
            cases.append((bad, "host-a"))
        bad = evidence()
        bad["hosts"][0]["cpu_percent"] = 80.1
        cases.append((bad, "host-a"))
        bad = evidence()
        bad["outage"]["samples"][-1]["elapsed_ms"] = 32000
        cases.append((bad, "maximum_recovery_ms"))
        for bad, expected in cases:
            with self.subTest(expected=expected):
                result = check(bad)
                self.assertEqual(result["status"], "fail")
                self.assertTrue(any(expected in failure for failure in result["failures"]), result)

    def test_dirty_simulated_or_single_host_evidence_rejected(self):
        bad = evidence()
        bad["clean_worktree"] = False
        self.assertIn("clean_worktree_required", check(bad)["failures"])
        for field, value in (("measurement_class", "simulated_process_local"),
                             ("commit_sha", "1" * 39), ("attempt", True),
                             ("workflow_url", "http://example.org/run")):
            bad = evidence()
            bad[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                check(bad)
        bad = evidence()
        bad["hosts"] = bad["hosts"][:1]
        with self.assertRaises(ValueError):
            check(bad)

    def test_inconsistent_accounting_latency_and_identity_rejected(self):
        bad = evidence()
        bad["delivery"]["sent"] += 1
        with self.assertRaises(ValueError):
            check(bad)
        bad = evidence()
        bad["delivery"]["delivered"] -= 1
        with self.assertRaises(ValueError):
            check(bad)
        bad = evidence()
        bad["latency_ms"]["count"] -= 1
        with self.assertRaises(ValueError):
            check(bad)
        bad = evidence()
        bad["latency_ms"]["p95"] = 2001
        with self.assertRaises(ValueError):
            check(bad)
        bad = evidence()
        bad["latency_ms"]["p50"] = float("nan")
        with self.assertRaises(ValueError):
            check(bad)
        bad = evidence()
        bad["hosts"][1]["host_id"] = "host-a"
        with self.assertRaises(ValueError):
            check(bad)
        bad = evidence()
        bad["hosts"][0]["binary_sha256"] = "not-a-hash"
        with self.assertRaises(ValueError):
            check(bad)

    def test_time_outage_and_policy_shape_rejected(self):
        bad = evidence()
        bad["started_at"] = "2026-11-07T00:00:00"
        with self.assertRaises(ValueError):
            check(bad)
        bad = evidence()
        bad["workload"]["duration_seconds"] = 902
        with self.assertRaises(ValueError):
            check(bad)
        bad = evidence()
        bad["outage"]["kind"] = "simulated_disconnect"
        with self.assertRaises(ValueError):
            check(bad)
        bad = evidence()
        bad["outage"]["samples"] = bad["outage"]["samples"][:2]
        self.assertIn("maximum_recovery_ms", check(bad)["failures"])
        policy = copy.deepcopy(POLICY)
        del policy["maximum_recovery_ms"]
        with self.assertRaises(ValueError):
            gate.check(evidence(), policy)
        policy = copy.deepcopy(POLICY)
        policy["maximum_latency_p95_ms"] = 3000
        with self.assertRaises(ValueError):
            gate.check(evidence(), policy)

    def test_cli_emits_result_and_exit_status(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            path.write_text(json.dumps(evidence()), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                code = gate.main([str(path)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "pass")
            bad = evidence()
            bad["clean_worktree"] = False
            path.write_text(json.dumps(bad), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                code = gate.main([str(path)])
            self.assertEqual(code, 1)
            self.assertIn("clean_worktree_required", json.loads(output.getvalue())["failures"])


if __name__ == "__main__":
    unittest.main()
