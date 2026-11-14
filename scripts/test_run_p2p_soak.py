"""Fail-closed measurement and lifecycle regressions (no network needed)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from run_p2p_soak import Agent, Controller, measurement_class, percentile, workload_records


class MeasurementTests(unittest.TestCase):
    def test_workload_records_preserve_lost_late_and_duplicate_observations(self):
        c = Controller()
        c.command = lambda *args, **kwargs: None
        with patch('run_p2p_soak.time.monotonic', side_effect=[11.0, 12.0, 14.0, 17.0]):
            keys = [c.publish('host-a', 0, str(i)) for i in range(4)]
        for digest, when in ((keys[0], 11.1), (keys[1], 14.0), (keys[1], 14.5)):
            c.events.put(('host-b', {'kind': 'delivery', 'digest': digest}))
            with patch('run_p2p_soak.time.monotonic', return_value=when):
                c.drain()
        records = workload_records(c, keys, 10, outage_start_ms=1500,
                                   restart_ms=3500, recovered_ms=5500)
        self.assertEqual([r['published_elapsed_ms'] for r in records], [1000, 2000, 4000, 7000])
        self.assertEqual([r['during_outage'] for r in records], [False, True, True, False])
        self.assertEqual([r['while_sink_stopped'] for r in records], [False, True, False, False])
        self.assertEqual(records[1]['delivery_count'], 2)
        self.assertEqual(records[1]['duplicate_deliveries'], 1)
        self.assertEqual(records[1]['delivered_elapsed_ms'], 4000)
        self.assertEqual(records[1]['latency_ms'], 2000)
        self.assertTrue(records[1]['delivered_after_restart'])
        self.assertTrue(records[1]['outage_publication_delivered_after_restart'])
        self.assertFalse(records[0]['delivered_after_restart'])
        self.assertEqual(records[2]['outcome'], 'lost')
        self.assertIsNone(records[2]['latency_ms'])
        self.assertEqual(records[2]['delivery_count'], 0)

    def test_wrong_recipient_and_untracked_delivery_cannot_be_credited(self):
        for host, digest, expected in (
            ('host-c', 'tracked', 'unexpected_decryption_recipient'),
            ('host-b', 'unknown', 'untracked_delivery_observation'),
        ):
            c = Controller()
            c.sent = {'tracked': 1.0}
            c.events.put((host, {'kind': 'delivery', 'digest': digest}))
            with self.subTest(host=host), self.assertRaisesRegex(RuntimeError, expected):
                c.drain()
            self.assertEqual(c.deliveries, {})
            self.assertEqual(c.delivery_counts, {})

    def test_aborted_window_records_do_not_claim_permanent_loss(self):
        c = Controller()
        c.sent, c.sources = {'pending': 12.0}, {'pending': 'host-a'}
        records = workload_records(c, ['pending'], 10, outage_start_ms=1500,
                                   restart_ms=None, recovered_ms=None, complete=False)
        self.assertEqual(records[0]['outcome'], 'pending_at_abort')
        self.assertTrue(records[0]['during_outage'])
        self.assertTrue(records[0]['while_sink_stopped'])
        self.assertIsNone(records[0]['delivered_elapsed_ms'])

    def test_stale_subscriptions_cannot_release_restart_barrier(self):
        c = Controller()
        subscription = {'kind': 'subscription', 'node': 0, 'peer': 'sink'}
        c.events.put(('host-a', subscription))
        c.drain()
        self.assertIn(('host-a', 0, 'sink'), c.subscriptions)
        c.events.put(('host-b', {'kind': 'sink_stopped'}))
        c.drain()
        self.assertEqual(c.subscriptions, set())
        c.events.put(('host-a', subscription))
        c.drain()
        c.events.put(('host-a', {'kind': 'disconnected', 'node': 0, 'peer': 'sink'}))
        c.drain()
        self.assertEqual(c.subscriptions, set())

    def test_late_probe_still_proves_recovery_and_old_probe_cannot(self):
        c = Controller()
        c.sent = {'old': 1.0, 'new': 6.0}
        c.deliveries = {'old': 9.0}
        pending = {'old': 1.0, 'new': 6.0}
        with patch('run_p2p_soak.time.monotonic', return_value=10.0):
            self.assertFalse(c.observe_probes(pending, 0, restart_after=5.0))
        self.assertIn('new', pending)
        c.deliveries['new'] = 10.0  # Four seconds, later than a sampling tick.
        with patch('run_p2p_soak.time.monotonic', return_value=10.1):
            self.assertTrue(c.observe_probes(pending, 0, restart_after=5.0))
        self.assertEqual(pending, {})

    def test_publishers_enforce_declared_star_and_ignore_inherited_operator_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(binary=Path('/bin/true'), port=0, controller_pid=None)
            with patch('run_p2p_soak.subprocess.Popen') as launch, \
                 patch('run_p2p_soak.threading.Thread'), \
                 patch.dict(os.environ, {'OSIRIS_TRUSTED_PEERS': 'operator-peer',
                                         'OSIRIS_TRUSTED_PEERS_FILE': '/operator/private-policy'}):
                agent = Agent(args, tmp)
                agent.start(1, '/ip4/127.0.0.1/tcp/1/p2p/recipient', 'recipient')
                env = launch.call_args.kwargs['env']
                self.assertEqual(env['OSIRIS_TRUSTED_PEERS'], 'recipient')
                self.assertEqual(env['OSIRIS_E2EE_RECIPIENTS'], 'recipient')
                self.assertNotIn('OSIRIS_TRUSTED_PEERS_FILE', env)
                self.assertEqual(env['OSIRIS_E2EE_ENABLED'], '1')
                self.assertEqual(env['OSIRIS_MAX_CONNECTIONS_PER_PEER'], '1')
                self.assertEqual(env['OSIRIS_AUTO_RELAY'], '0')
                self.assertEqual(env['OSIRIS_BOOTSTRAP_SEED_URL'], 'off')

    def test_same_machine_with_different_labels_is_never_multi_host(self):
        hosts = {'a': {'host_fingerprint': 'same'}, 'b': {'host_fingerprint': 'same'}}
        self.assertEqual(measurement_class(hosts, False), 'single_host_process_regression')
        hosts['b']['host_fingerprint'] = 'different'
        self.assertEqual(measurement_class(hosts, True), 'single_host_process_regression')
        self.assertEqual(measurement_class(hosts, False), 'operator_attested_real_multi_host')
        hosts['c'] = {'host_fingerprint': 'third'}
        hosts['d'] = {'host_fingerprint': 'fourth'}
        self.assertEqual(measurement_class(hosts, False), 'operator_attested_real_multi_host')
        hosts['d']['host_fingerprint'] = 'third'
        self.assertEqual(measurement_class(hosts, False), 'single_host_process_regression')

    def test_percentiles_use_nearest_rank_and_keep_tail_outlier(self):
        values = list(range(1, 101))
        self.assertEqual(percentile(values, .95), 95)
        self.assertEqual(percentile(values, .99), 99)
        self.assertEqual(percentile([7000], .99), 7000)

    def test_outage_samples_never_invent_times_for_colliding_ticks(self):
        c = Controller()
        with patch('run_p2p_soak.time.monotonic', side_effect=[10.001, 10.001, 10.003]):
            c.sample_outage(10, True)
            c.sample_outage(10, False)
            c.sample_outage(10, False)
        self.assertEqual(len(c.samples), 2)
        self.assertLess(c.samples[0]['elapsed_ms'], c.samples[1]['elapsed_ms'])
        self.assertFalse(c.samples[-1]['delivered'])

    def test_unexpected_eof_and_node_exit_invalidate_measurement(self):
        for event in ({'kind': 'eof'}, {'kind': 'fatal', 'reason': 'node_exited'}):
            c = Controller()
            c.events.put(('host-b', event))
            with self.assertRaises(RuntimeError):
                c.drain()

    def test_only_explicitly_finished_agent_may_close_its_stream(self):
        c = Controller()
        c.events.put(('host-a', {'kind': 'finished'}))
        c.events.put(('host-a', {'kind': 'eof'}))
        c.drain()
        c.drain()
        self.assertIsNone(c.failure)

    @unittest.skipUnless(sys.platform == 'linux', 'agent resource collector uses procfs')
    def test_agent_eof_cleans_child_and_temporary_state(self):
        with tempfile.TemporaryDirectory(prefix='osiris-soak-test-') as tmp:
            node = Path(tmp) / 'node'
            marker = Path(tmp) / 'child.json'
            node.write_text(f'#!{sys.executable}\nimport json,os,signal\n'
                            f'open({str(marker)!r}, "w").write(json.dumps('
                            '{"pid":os.getpid(),"state":os.environ["OSIRIS_NODE_KEY"]}))\n'
                            'print("peer_id=test-peer",flush=True)\n'
                            'while True: signal.pause()\n')
            node.chmod(0o700)
            agent = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name('run_p2p_soak.py')),
                 '--agent', '--binary', str(node), '--port', '19001'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                agent.stdin.write('{"action":"sink"}\n')
                agent.stdin.flush()
                while True:
                    event = json.loads(agent.stdout.readline())
                    if event['kind'] == 'peer':
                        break
                child = json.loads(marker.read_text())
                agent.communicate(timeout=15)
                self.assertEqual(agent.returncode, 0)
                self.assertFalse(Path(child['state']).parent.exists())
                with self.assertRaises(ProcessLookupError):
                    os.kill(child['pid'], 0)
            finally:
                if agent.poll() is None:
                    agent.kill()
                    agent.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
