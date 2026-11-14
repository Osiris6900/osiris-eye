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



def traffic_report():
    data = report()
    data['measurement_notes'].update({
        'traffic_during_outage': True, 'traffic_mode': 'continuous_during_outage',
        'workload_paused_during_outage': False, 'workload_drained_before_outage': False,
        'workload_retransmissions': 0,
    })
    data['outage'].update(outage_start_ms=450000, sink_restart_ms=455000,
                          all_publishers_recovered_ms=460000, samples=[
                              {'elapsed_ms': 0, 'connected': True, 'delivered': True},
                              {'elapsed_ms': 450000, 'connected': False, 'delivered': False},
                              {'elapsed_ms': 460000, 'connected': True, 'delivered': True},
                          ])
    records = []
    for i in range(10000):
        published = i * 80
        received = published + 100
        records.append({
            'payload_sha256': hashlib.sha256(str(i).encode()).hexdigest(),
            'source_host': 'host-a', 'recipient_host': 'host-b',
            'published_elapsed_ms': published,
            'during_outage': 450000 <= published < 460000,
            'while_sink_stopped': 450000 <= published < 455000,
            'outcome': 'delivered', 'delivery_count': 1, 'duplicate_deliveries': 0,
            'delivered_elapsed_ms': received, 'delivered_after_restart': received >= 455000,
            'outage_publication_delivered_after_restart': 450000 <= published < 460000 and received >= 455000,
            'latency_ms': 100,
        })
    data['delivery_records'] = records
    data['workload'].update(messages_while_sink_stopped=sum(r['while_sink_stopped'] for r in records),
                            messages_during_outage=sum(r['during_outage'] for r in records))
    data['delivery'].update(delivered=10000, rejected=0, duplicate_deliveries=0,
                            delivered_after_restart=sum(r['delivered_after_restart'] for r in records))
    data['delivery']['outage_publications_delivered_after_restart'] = sum(
        r['outage_publication_delivered_after_restart'] for r in records)
    data['latency_ms'].update(count=10000, p50=100, p95=100, p99=100, max=100)
    return data


def review(data, *, commit=SHA, url=URL, attempt=1):
    return validate(data, commit=commit, run_url=url, attempt=attempt,
                    policy=copy.deepcopy(POLICY))


class SustainedCapacityReviewTests(unittest.TestCase):
    def test_traffic_on_windows_are_checked_without_promoting_acceptance(self):
        result = review(traffic_report())
        self.assertTrue(result['passed_offline_consistency'], result['errors'])
        self.assertEqual(result['declared_traffic_mode'], 'continuous_during_outage')
        self.assertFalse(result['t04_t19_release_gates_accepted'])
        self.assertFalse(result['independent_host_identity_verified'])

    def test_traffic_on_flag_without_actual_publication_records_is_refused(self):
        data = traffic_report()
        del data['delivery_records']
        self.assertIn('traffic_on_exact_workload_records_required', review(data)['errors'])
        data = traffic_report()
        for record in data['delivery_records']:
            record['published_elapsed_ms'] = 0
            record['during_outage'] = record['while_sink_stopped'] = False
        data['workload'].update(messages_while_sink_stopped=0, messages_during_outage=0)
        self.assertIn('traffic_on_no_workload_while_sink_stopped', review(data)['errors'])

    def test_traffic_windows_totals_and_delivery_counts_cannot_be_forged(self):
        for field, value in (('during_outage', True), ('while_sink_stopped', True),
                             ('duplicate_deliveries', 1), ('delivered_after_restart', True),
                             ('delivery_count', True), ('published_elapsed_ms', -1),
                             ('source_host', 'host-z'), ('recipient_host', 'host-c')):
            data = traffic_report()
            data['delivery_records'][0][field] = value
            with self.subTest(field=field):
                self.assertFalse(review(data)['passed_offline_consistency'])
        data = traffic_report()
        data['workload']['messages_while_sink_stopped'] += 1
        self.assertIn('traffic_on_publication_counts_mismatch', review(data)['errors'])
        data = traffic_report()
        data['delivery_records'][1]['payload_sha256'] = data['delivery_records'][0]['payload_sha256']
        self.assertFalse(review(data)['passed_offline_consistency'])
        for boundary in ('sink_restart_ms', 'all_publishers_recovered_ms'):
            data = traffic_report()
            data['outage'][boundary] = None
            with self.subTest(boundary=boundary):
                self.assertFalse(review(data)['passed_offline_consistency'])

    def test_traffic_latency_is_recomputed_from_observed_records(self):
        data = traffic_report()
        data['delivery_records'][0]['latency_ms'] = 1
        self.assertIn('traffic_on_latency_time_mismatch', review(data)['errors'])
        data = traffic_report()
        data['latency_ms']['p95'] = 1
        self.assertIn('traffic_on_latency_summary_mismatch', review(data)['errors'])

    def test_traffic_observations_cannot_extend_beyond_reported_cutoff(self):
        for field in ('published_elapsed_ms', 'delivered_elapsed_ms'):
            data = traffic_report()
            data['delivery_records'][0][field] = 900003
            with self.subTest(field=field):
                self.assertFalse(review(data)['passed_offline_consistency'])
        data = traffic_report()
        data['outage']['all_publishers_recovered_ms'] = 900003
        self.assertIn('traffic_on_recovery_after_observation_cutoff', review(data)['errors'])

    def test_actual_traffic_on_losses_remain_failures_of_the_unchanged_policy(self):
        data = traffic_report()
        row = next(r for r in data['delivery_records'] if r['while_sink_stopped'])
        was_late = row['delivered_after_restart']
        was_recovered = row['outage_publication_delivered_after_restart']
        row.update(outcome='lost', delivery_count=0, delivered_elapsed_ms=None,
                   delivered_after_restart=False, outage_publication_delivered_after_restart=False, latency_ms=None)
        data['delivery']['delivered'] -= 1
        data['delivery']['lost'] += 1
        data['delivery']['delivered_after_restart'] -= int(was_late)
        data['delivery']['outage_publications_delivered_after_restart'] -= int(was_recovered)
        data['latency_ms']['count'] -= 1
        result = review(data)
        self.assertIn('predeclared_load_policy_not_met', result['errors'])
        self.assertFalse(result['t04_t19_release_gates_accepted'])

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
