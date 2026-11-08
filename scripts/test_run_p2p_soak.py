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

from run_p2p_soak import Agent, Controller, measurement_class, percentile


class MeasurementTests(unittest.TestCase):
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
