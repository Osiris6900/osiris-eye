"""Negative tests for opt-in sustained P2P evidence scope and run binding."""
from __future__ import annotations

import copy
import hashlib
import unittest

from check_p2p_sustained_evidence import validate
from test_check_p2p_load_acceptance import POLICY, evidence

SHA = "1" * 40
URL = "https://github.com/Osiris6900/osiris-eye/actions/runs/123"
HOST_IDS = ("host-a", "host-b", "host-c")


def report():
    data = evidence()
    data["workload"]["fanout"] = 1
    data["hosts"] = [
        {
            "host_id": host,
            "binary_sha256": hashlib.sha256((host + "binary").encode()).hexdigest(),
            "host_fingerprint_commitment": hashlib.sha256((host + "boot").encode()).hexdigest(),
            "rss_peak_bytes": 100_000_000,
            "cpu_percent": 20,
            "disk_growth_bytes": 100,
        }
        for host in HOST_IDS
    ]
    data["measurement_notes"] = {
        "open_mesh_capacity_tested": False,
        "workload_paused_during_outage": True,
        "independent_host_fingerprints_distinct": True,
    }
    return data


def review(data, *, commit=SHA, url=URL, attempt=1):
    return validate(data, commit=commit, run_url=url, attempt=attempt,
                    policy=copy.deepcopy(POLICY))


class SustainedCapacityReviewTests(unittest.TestCase):
    def test_exact_report_is_offline_only_and_requires_independent_review(self):
        result = review(report())
        self.assertTrue(result["passed_offline_consistency"], result["errors"])
        self.assertFalse(result["github_workflow_conclusion_verified"])
        self.assertFalse(result["independent_host_identity_verified"])
        self.assertFalse(result["production_network_or_open_mesh_verified"])
        self.assertFalse(result["t04_t19_release_gates_accepted"])

    def test_wrong_sha_run_attempt_and_dirty_tree_fail_closed(self):
        for mutation in (
            ("commit_sha", "a" * 40),
            ("workflow_url", URL + "1"),
            ("attempt", 2),
            ("clean_worktree", False),
        ):
            data = report()
            data[mutation[0]] = mutation[1]
            with self.subTest(field=mutation[0]):
                self.assertFalse(review(data)["passed_offline_consistency"])
        self.assertFalse(review(report(), commit="a" * 40)["passed_offline_consistency"])

    def test_short_or_small_workloads_do_not_pass(self):
        cases = [
            ("peers", 99),
            ("messages", 9999),
            ("fanout", 8),
            ("duration_seconds", 899),
        ]
        for field, value in cases:
            data = report()
            data["workload"][field] = value
            if field == "duration_seconds":
                data["ended_at"] = "2026-11-07T00:14:59Z"
            with self.subTest(field=field):
                self.assertFalse(review(data)["passed_offline_consistency"])

    def test_forged_host_and_scope_claims_are_rejected(self):
        bad = report()
        bad["hosts"][2]["host_fingerprint_commitment"] = bad["hosts"][0]["host_fingerprint_commitment"]
        self.assertFalse(review(bad)["passed_offline_consistency"])
        bad = report()
        bad["hosts"][2]["host_id"] = "host-b"
        self.assertFalse(review(bad)["passed_offline_consistency"])
        bad = report()
        bad["hosts"][0]["binary_sha256"] = "not-a-hash"
        self.assertFalse(review(bad)["passed_offline_consistency"])
        for key, value in (
            ("open_mesh_capacity_tested", True),
            ("workload_paused_during_outage", False),
            ("independent_host_fingerprints_distinct", False),
        ):
            bad = report()
            bad["measurement_notes"][key] = value
            with self.subTest(key=key):
                self.assertFalse(review(bad)["passed_offline_consistency"])

    def test_losses_or_threshold_failures_do_not_become_release_acceptance(self):
        bad = report()
        bad["latency_ms"]["p95"] = 1100
        self.assertFalse(review(bad)["passed_offline_consistency"])
        bad = report()
        bad["delivery"]["lost"] = 1
        bad["delivery"]["delivered"] = 9900
        bad["latency_ms"]["count"] = 9900
        result = review(bad)
        self.assertFalse(result["passed_offline_consistency"])
        self.assertFalse(result["t04_t19_release_gates_accepted"])


if __name__ == "__main__":
    unittest.main()
