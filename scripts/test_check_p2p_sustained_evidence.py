"""Negative tests for opt-in sustained P2P evidence scope and run binding."""
from __future__ import annotations

import copy
import hashlib
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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



def traffic_report(*, recovered_ms=460000):
    data = report()
    data['measurement_notes'].update({
        'traffic_during_outage': True, 'traffic_mode': 'continuous_during_outage',
        'workload_paused_during_outage': False, 'workload_drained_before_outage': False,
        'workload_retransmissions': 0,
    })
    data['outage'].update(outage_start_ms=450000, sink_restart_ms=455000,
                          all_publishers_recovered_ms=recovered_ms, samples=[
                              {'elapsed_ms': 0, 'connected': True, 'delivered': True},
                              {'elapsed_ms': 450000, 'connected': False, 'delivered': False},
                              {'elapsed_ms': 460000, 'connected': True, 'delivered': True},
                          ])
    records = []
    for i in range(10000):
        published = i * 80
        # Workload submitted while the sink is stopped can only be observed
        # after it restarts. These are consistency fixtures, not real network
        # evidence or a claim that gossipsub guarantees queued delivery.
        received = max(published + 100, 455100) if 450000 <= published < 455000 else published + 100
        records.append({
            'payload_sha256': hashlib.sha256(str(i).encode()).hexdigest(),
            'source_host': 'host-a', 'recipient_host': 'host-b',
            'published_elapsed_ms': published,
            'during_outage': 450000 <= published < recovered_ms,
            'while_sink_stopped': 450000 <= published < 455000,
            'outcome': 'delivered', 'delivery_count': 1, 'duplicate_deliveries': 0,
            'delivered_elapsed_ms': received, 'delivered_after_restart': received >= 455000,
            'outage_publication_delivered_after_restart': 450000 <= published < recovered_ms and received >= 455000,
            'latency_ms': received - published,
        })
    data['delivery_records'] = records
    data['workload'].update(messages_while_sink_stopped=sum(r['while_sink_stopped'] for r in records),
                            messages_during_outage=sum(r['during_outage'] for r in records))
    data['delivery'].update(delivered=10000, rejected=0, duplicate_deliveries=0,
                            delivered_after_restart=sum(r['delivered_after_restart'] for r in records))
    data['delivery']['outage_publications_delivered_after_restart'] = sum(
        r['outage_publication_delivered_after_restart'] for r in records)
    update_latency_summary(data)
    return data


def update_latency_summary(data):
    ordered = sorted(row['latency_ms'] for row in data['delivery_records']
                     if row['outcome'] == 'delivered')
    data['latency_ms'] = {'count': len(ordered), **{
        name: ordered[math.ceil(len(ordered) * q) - 1]
        for name, q in (('p50', .5), ('p95', .95), ('p99', .99), ('max', 1))
    }}


def review(data, *, commit=SHA, url=URL, attempt=1):
    return validate(data, commit=commit, run_url=url, attempt=attempt,
                    policy=copy.deepcopy(POLICY))


class SustainedCapacityReviewTests(unittest.TestCase):
    def test_publisher_reconnect_configuration_is_bound_to_every_host(self):
        data = report()
        runtime = {
            'node_worker_threads_requested': 1,
            'tokio_worker_threads_env': '1',
            'observed_runtime_worker_count_verified': False,
            'publisher_outbound_queue': True,
            'publisher_bootstrap_reconnect_seconds': 1,
        }
        data['runtime_config'] = runtime
        for host in data['hosts']:
            host['runtime_config'] = dict(runtime)
        self.assertTrue(review(data)['passed_offline_consistency'])
        data['hosts'][0]['runtime_config']['publisher_bootstrap_reconnect_seconds'] = True
        self.assertIn('host_runtime_configuration_mismatch', review(data)['errors'])
        runtime.pop('publisher_outbound_queue')
        self.assertIn('runtime_configuration_invalid', review(data)['errors'])

    def test_unhashable_host_id_returns_structured_failure(self):
        data = report()
        data['hosts'][0]['host_id'] = []
        result = review(data)
        self.assertFalse(result['passed_offline_consistency'])
        self.assertIn('host_record_invalid', result['errors'])

    def test_oversized_numeric_evidence_returns_structured_failure(self):
        data = traffic_report()
        data['workload']['duration_seconds'] = 10 ** 400
        result = review(data)
        self.assertFalse(result['passed_offline_consistency'])
        self.assertIn('traffic_on_observation_duration_invalid', result['errors'])
        self.assertIn('predeclared_load_policy_not_met', result['errors'])

        data = traffic_report()
        data['delivery_records'][0]['latency_ms'] = 10 ** 400
        self.assertIn('traffic_on_delivery_invalid', review(data)['errors'])

    def test_deeply_nested_report_writes_failed_cli_verdict(self):
        checker = Path(__file__).with_name('check_p2p_sustained_evidence.py')
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory) / 'report.json'
            output = Path(directory) / 'verdict.json'
            raw.write_text('[' * 10000 + '0' + ']' * 10000, encoding='utf-8')
            result = subprocess.run(
                [sys.executable, str(checker), '--report', str(raw), '--commit', SHA,
                 '--run-url', URL, '--attempt', '1', '--output', str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn('cannot_read_or_parse_report', output.read_text(encoding='utf-8'))

    def test_requested_runtime_is_consistent_across_all_hosts(self):
        for count in (None, 1, 2, 4):
            data = report()
            runtime = {'node_worker_threads_requested': count,
                       'tokio_worker_threads_env': None if count is None else str(count),
                       'observed_runtime_worker_count_verified': False}
            data['runtime_config'] = runtime
            for host in data['hosts']:
                host['runtime_config'] = dict(runtime)
            with self.subTest(count=count):
                self.assertTrue(review(data)['passed_offline_consistency'])
            data['hosts'][0]['runtime_config'] = {'node_worker_threads_requested': 3}
            self.assertIn('host_runtime_configuration_mismatch', review(data)['errors'])
        data = report()
        runtime = {'node_worker_threads_requested': 1, 'tokio_worker_threads_env': '1',
                   'observed_runtime_worker_count_verified': False}
        data['runtime_config'] = runtime
        for host in data['hosts']:
            host['runtime_config'] = dict(runtime)
        data['hosts'][0]['runtime_config']['node_worker_threads_requested'] = True
        self.assertIn('host_runtime_configuration_mismatch', review(data)['errors'])
        for invalid in (True, 0, 3, 8, '1'):
            data = report()
            runtime = {'node_worker_threads_requested': invalid,
                       'tokio_worker_threads_env': str(invalid),
                       'observed_runtime_worker_count_verified': False}
            data['runtime_config'] = runtime
            for host in data['hosts']:
                host['runtime_config'] = runtime
            with self.subTest(invalid=invalid):
                self.assertIn('runtime_configuration_invalid', review(data)['errors'])

    def test_traffic_on_windows_are_checked_without_promoting_acceptance(self):
        result = review(traffic_report())
        self.assertTrue(result['passed_offline_consistency'], result['errors'])
        self.assertEqual(result['declared_traffic_mode'], 'continuous_during_outage')
        self.assertFalse(result['t04_t19_release_gates_accepted'])
        self.assertFalse(result['independent_host_identity_verified'])

    def test_delivery_inside_stopped_sink_window_is_refused(self):
        data = traffic_report()
        row = next(r for r in data['delivery_records']
                   if r['while_sink_stopped'] and r['published_elapsed_ms'] > 450001)
        row.update(delivered_elapsed_ms=450200, latency_ms=450200 - row['published_elapsed_ms'],
                   delivered_after_restart=False, outage_publication_delivered_after_restart=False)
        data['delivery']['delivered_after_restart'] -= 1
        data['delivery']['outage_publications_delivered_after_restart'] -= 1
        update_latency_summary(data)
        self.assertIn('traffic_on_delivery_while_sink_stopped', review(data)['errors'])

    def test_stopped_sink_delivery_boundaries_allow_only_one_ms_rounding(self):
        for received, allowed in ((454998, False), (454999, True), (455000, True)):
            data = traffic_report()
            row = next(r for r in data['delivery_records']
                       if r['while_sink_stopped'] and r['published_elapsed_ms'] > 450001)
            after_restart = received >= 455000
            row.update(delivered_elapsed_ms=received, latency_ms=received - row['published_elapsed_ms'],
                       delivered_after_restart=after_restart,
                       outage_publication_delivered_after_restart=after_restart)
            data['delivery']['delivered_after_restart'] -= int(not after_restart)
            data['delivery']['outage_publications_delivered_after_restart'] -= int(not after_restart)
            update_latency_summary(data)
            with self.subTest(received=received):
                result = review(data)
                self.assertEqual(result['passed_offline_consistency'], allowed, result['errors'])

    def test_stop_request_clock_tick_does_not_imply_confirmed_sink_downtime(self):
        for published, allowed in ((450000, True), (450001, True), (450002, False)):
            data = traffic_report()
            row = next(r for r in data['delivery_records'] if r['while_sink_stopped'])
            row.update(published_elapsed_ms=published, delivered_elapsed_ms=450100,
                       latency_ms=450100 - published, delivered_after_restart=False,
                       outage_publication_delivered_after_restart=False)
            data['delivery']['delivered_after_restart'] -= 1
            data['delivery']['outage_publications_delivered_after_restart'] -= 1
            update_latency_summary(data)
            with self.subTest(published=published):
                result = review(data)
                self.assertEqual(result['passed_offline_consistency'], allowed, result['errors'])

    def test_fast_probe_cannot_hide_all_publisher_recovery_over_policy(self):
        for recovered_ms, allowed in ((479999, True), (480000, True), (480001, False), (899999, False)):
            data = traffic_report(recovered_ms=recovered_ms)
            with self.subTest(recovered_ms=recovered_ms):
                result = review(data)
                self.assertEqual(result['passed_offline_consistency'], allowed, result['errors'])
                if not allowed:
                    self.assertIn('traffic_on_all_publishers_recovery_limit_exceeded', result['errors'])

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
