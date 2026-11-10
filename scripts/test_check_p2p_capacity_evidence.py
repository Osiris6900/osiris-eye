"""Offline regression tests for three-sweep capacity evidence validation."""
from __future__ import annotations

import copy
import hashlib
import unittest

from check_p2p_capacity_evidence import validate_reports

SHA = "a" * 40
URL = "https://github.com/Osiris6900/osiris-eye/actions/runs/42799999999"
COMMITMENTS = {host: hashlib.sha256(host.encode()).hexdigest()
               for host in ("host-a", "host-b", "host-c")}


def reports():
    out = {}
    for peers in (10, 50, 100):
        records = [
            {"payload_sha256": hashlib.sha256(f"{peers}/{i}".encode()).hexdigest(),
             "outcome": "delivered", "latency_ms": 10.0,
             "source_host": "host-a", "recipient_host": "host-b"}
            for i in range(200)
        ]
        out[peers] = {
            "schema": "osiris.p2p_load_acceptance.v1",
            "measurement_class": "operator_attested_real_multi_host",
            "commit_sha": SHA,
            "clean_worktree": True,
            "workflow_url": URL,
            "attempt": 1,
            "operator": "github-actions",
            "workload": {"peers": peers, "messages": 200,
                         "duration_seconds": 30.01, "fanout": 1},
            "hosts": [
                {"host_id": host, "binary_sha256": "b" * 64,
                 "host_fingerprint_commitment": COMMITMENTS[host],
                 "rss_peak_bytes": 100, "cpu_percent": 1, "disk_growth_bytes": 0}
                for host in ("host-a", "host-b", "host-c")
            ],
            "delivery": {"sent": 200, "delivered": 200, "lost": 0,
                         "pending": 0, "rejected": 0},
            "delivery_records": records,
            "latency_ms": {"count": 200, "p50": 10, "p95": 10,
                           "p99": 10, "max": 10},
            "outage": {"kind": "bootstrap", "samples": [
                {"elapsed_ms": 5000, "connected": False, "delivered": False},
                {"elapsed_ms": 10000, "connected": True, "delivered": True},
            ]},
            "measurement_notes": {
                "independent_host_fingerprints_distinct": True,
                "open_mesh_capacity_tested": False,
                "workload_paused_during_outage": True,
            },
        }
    return out


def check(data):
    return validate_reports(data, expected_commit=SHA, expected_run_url=URL,
                            expected_attempt=1)


class CapacityEvidenceTests(unittest.TestCase):
    def test_consistent_reports_are_review_required_not_accepted(self):
        result = check(reports())
        self.assertTrue(result["passed_offline_consistency"], result["errors"])
        self.assertFalse(result["t04_t19_gate_accepted"])
        self.assertFalse(result["external_run_status_verified"])
        self.assertFalse(result["independent_hosts_externally_attested"])
        self.assertTrue(result["review_required"])

    def test_missing_sweep_fails_closed(self):
        data = reports()
        del data[100]
        self.assertFalse(check(data)["passed_offline_consistency"])

    def test_mismatched_commit_fails_closed(self):
        data = reports()
        data[50]["commit_sha"] = "c" * 40
        self.assertFalse(check(data)["passed_offline_consistency"])

    def test_report_not_real_multi_host_fails_closed(self):
        data = reports()
        data[10]["measurement_class"] = "single_host_process_regression"
        self.assertFalse(check(data)["passed_offline_consistency"])

    def test_duplicate_hosts_and_cross_sweep_changes_fail_closed(self):
        data = reports()
        data[50]["hosts"][1]["host_fingerprint_commitment"] = COMMITMENTS["host-a"]
        self.assertFalse(check(data)["passed_offline_consistency"])
        data = reports()
        data[100]["hosts"][2]["host_fingerprint_commitment"] = "d" * 64
        self.assertIn(
            "cross_sweep_host_commitments_changed", check(data)["errors"],
        )

    def test_missing_binary_hash_fails_closed(self):
        data = reports()
        del data[100]["hosts"][0]["binary_sha256"]
        self.assertFalse(check(data)["passed_offline_consistency"])

    def test_delivery_lost_reconciles_and_marks_degraded(self):
        data = reports()
        rec = data[100]["delivery_records"][-1]
        rec["outcome"] = "lost"
        rec["latency_ms"] = None
        data[100]["delivery"].update({"delivered": 199, "lost": 1})
        data[100]["latency_ms"]["count"] = 199
        result = check(data)
        self.assertTrue(result["passed_offline_consistency"], result["errors"])
        self.assertEqual(result["degraded_loss_sweeps"], [100])
        self.assertFalse(result["t04_t19_gate_accepted"])

    def test_inconsistent_delivery_and_duplicate_payload_fail_closed(self):
        data = reports()
        data[10]["delivery"]["delivered"] = 199
        self.assertFalse(check(data)["passed_offline_consistency"])
        data = reports()
        data[10]["delivery_records"][-1]["payload_sha256"] = \
            data[10]["delivery_records"][0]["payload_sha256"]
        self.assertFalse(check(data)["passed_offline_consistency"])

    def test_lie_about_outage_scope_or_run_attempt_fails_closed(self):
        data = reports()
        data[50]["measurement_notes"]["open_mesh_capacity_tested"] = True
        self.assertFalse(check(data)["passed_offline_consistency"])
        data = reports()
        data[50]["attempt"] = 2
        self.assertFalse(check(data)["passed_offline_consistency"])

    def test_nonfinite_latency_and_bool_counters_fail_closed(self):
        data = reports()
        data[10]["latency_ms"]["p95"] = float("nan")
        self.assertFalse(check(data)["passed_offline_consistency"])
        data = reports()
        data[10]["delivery"]["sent"] = True
        self.assertFalse(check(data)["passed_offline_consistency"])


if __name__ == "__main__":
    unittest.main()
