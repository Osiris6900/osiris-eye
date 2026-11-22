"""Fail-closed measurement and lifecycle regressions (no network needed)."""
import json
import os
import signal
import shlex
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from run_p2p_soak import Agent, Controller, measurement_class, percentile, workload_records, runtime_config, measure


class MeasurementTests(unittest.TestCase):
    def test_cleanup_signals_all_nodes_before_waiting_and_uses_one_grace_period(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(SimpleNamespace(controller_pid=None), tmp)
            events, clock = [], [10.0]
            for index in range(3):
                proc = Mock(pid=1000 + index)
                proc.poll.return_value = None
                def terminate(index=index, proc=proc):
                    proc.stdin.close.assert_called_once()
                    events.append(('terminate', index))
                proc.terminate.side_effect = terminate
                def wait(*, timeout, index=index):
                    events.append(('wait', index, timeout))
                    clock[0] += 3
                    return 0
                proc.wait.side_effect = wait
                agent.nodes[index] = proc
            with patch('run_p2p_soak.time.monotonic', side_effect=lambda: clock[0]):
                agent.cleanup()
            self.assertEqual(events[:3], [('terminate', index) for index in range(3)])
            self.assertEqual([event[2] for event in events[3:]], [8.0, 5.0, 2.0])
            self.assertTrue(agent.done.is_set())
            for proc in agent.nodes.values():
                proc.stdin.close.assert_called_once()
                proc.stdout.close.assert_called_once()

    def test_sink_restart_stop_closes_stdin_before_waiting_for_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(SimpleNamespace(controller_pid=None), tmp)
            proc = Mock(pid=1000)
            proc.terminate.side_effect = lambda: proc.stdin.close.assert_called_once()
            agent.nodes[0] = proc
            agent.stop_node(0)
            proc.terminate.assert_called_once()
            proc.wait.assert_called_once_with(timeout=8)
            proc.stdout.close.assert_called_once()

    def test_undrained_reader_cannot_be_reported_as_finished(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(SimpleNamespace(controller_pid=None, binary=Path('/bin/true')), tmp)
            proc, reader = Mock(pid=1500), Mock()
            proc.poll.return_value = None
            reader.is_alive.return_value = True
            agent.nodes[0], agent.node_readers[proc.pid] = proc, reader
            events = []
            agent.emit = lambda kind, **data: events.append(kind)
            with patch('run_p2p_soak.revision', return_value=('1' * 40, True)), \
                 patch('run_p2p_soak.digest_file', return_value='a' * 64), \
                 patch('run_p2p_soak.threading.Thread'), \
                 patch('run_p2p_soak.signal.signal'), \
                 patch('run_p2p_soak.sys.stdin', ['{"action":"finish"}\n']), \
                 patch.object(agent, 'sample', return_value={}):
                with self.assertRaisesRegex(RuntimeError, 'node_reader_not_drained:1500'):
                    agent.run()
            self.assertEqual(events, ['metadata', 'metrics'])
            proc.wait.assert_called_once()
            self.assertLessEqual(reader.join.call_args.kwargs['timeout'], 2)
            proc.stdout.close.assert_not_called()

    def test_controller_escalation_preserves_parent_reaping_and_bounds_final_wait(self):
        controller, proc = Controller(), Mock(pid=1600)
        proc.poll.return_value = None
        proc.wait.side_effect = [subprocess.TimeoutExpired('agent', 30),
                                subprocess.TimeoutExpired('agent', 10), 0]
        controller.agents['host-a'] = proc
        with patch('run_p2p_soak.os.killpg') as signal_group:
            controller.cleanup()
        proc.stdin.close.assert_called_once()
        proc.terminate.assert_called_once()
        signal_group.assert_called_once_with(proc.pid, signal.SIGKILL)
        self.assertEqual([call.kwargs['timeout'] for call in proc.wait.call_args_list], [30, 10, 5])

    def test_cleanup_kills_every_overdue_node_before_final_parent_waits(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(SimpleNamespace(controller_pid=None), tmp)
            events = []
            for index in range(2):
                proc = Mock(pid=2000 + index)
                proc.poll.return_value = None
                proc.terminate.side_effect = lambda index=index: events.append(('terminate', index))
                proc.kill.side_effect = lambda index=index: events.append(('kill', index))
                attempts = [0]
                def wait(*, timeout, index=index, attempts=attempts):
                    events.append(('wait', index))
                    attempts[0] += 1
                    if attempts[0] == 1:
                        raise subprocess.TimeoutExpired('node', timeout)
                    return 0
                proc.wait.side_effect = wait
                agent.nodes[index] = proc
            agent.cleanup()
            self.assertEqual(events, [('terminate', 0), ('terminate', 1),
                ('wait', 0), ('wait', 1), ('kill', 0), ('kill', 1), ('wait', 0), ('wait', 1)])

    def test_repeated_termination_signals_cannot_interrupt_parent_reaping(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(SimpleNamespace(controller_pid=None), tmp)
            proc = Mock(pid=3000)
            proc.poll.return_value = None
            observed = []
            def interrupted(kind, _frame):
                observed.append(kind)
            previous = {kind: signal.signal(kind, interrupted)
                        for kind in (signal.SIGTERM, signal.SIGHUP)}
            try:
                def wait(*, timeout):
                    os.kill(os.getpid(), signal.SIGTERM)
                    os.kill(os.getpid(), signal.SIGHUP)
                    return 0
                proc.wait.side_effect = wait
                agent.nodes[0] = proc
                agent.cleanup()
                self.assertEqual(observed, [])
                self.assertIs(signal.getsignal(signal.SIGTERM), interrupted)
                self.assertIs(signal.getsignal(signal.SIGHUP), interrupted)
                proc.wait.assert_called_once()
            finally:
                for kind, handler in previous.items():
                    signal.signal(kind, handler)

    def test_finished_event_waits_for_parent_cleanup_and_preserves_final_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(SimpleNamespace(controller_pid=None, binary=Path('/bin/true')), tmp)
            events = []
            agent.emit = lambda kind, **data: events.append(kind)
            with patch('run_p2p_soak.revision', return_value=('1' * 40, True)), \
                 patch('run_p2p_soak.digest_file', return_value='a' * 64), \
                 patch('run_p2p_soak.threading.Thread'), \
                 patch('run_p2p_soak.signal.signal'), \
                 patch('run_p2p_soak.sys.stdin', ['{"action":"finish"}\n']), \
                 patch.object(agent, 'sample', return_value={}), \
                 patch.object(agent, 'cleanup', side_effect=lambda: events.append('reaped')):
                agent.run()
            self.assertEqual(events, ['metadata', 'metrics', 'reaped', 'finished'])

    def test_bounded_worker_setting_reaches_actual_node_environment(self):
        for count in (None, 1, 2, 4):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as tmp:
                args = SimpleNamespace(binary=Path('/bin/true'), port=0, controller_pid=None,
                                       node_worker_threads=count)
                with patch('run_p2p_soak.subprocess.Popen') as launch, \
                     patch('run_p2p_soak.threading.Thread'), \
                     patch.dict(os.environ, {'TOKIO_WORKER_THREADS': '37'}):
                    agent = Agent(args, tmp)
                    agent.start(1)
                    environment = launch.call_args.kwargs['env']
                    self.assertEqual(environment.get('TOKIO_WORKER_THREADS'),
                                     None if count is None else str(count))
                    self.assertEqual(agent.runtime['node_worker_threads_requested'], count)
                    self.assertFalse(agent.runtime['observed_runtime_worker_count_verified'])
        for invalid in (True, 0, 3, 8, '1', 1.0):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'worker_threads'):
                runtime_config(invalid)

    def test_controller_propagates_worker_setting_to_every_remote_agent(self):
        for count in (None, 1, 2, 4):
            controller, launched = Controller(), {}
            args = SimpleNamespace(binary=Path('/bin/true'), remote_binary='/bin/true',
                remote_script='/workspace/scripts/run_p2p_soak.py', extra_host=['host-c=runner@host-c'],
                peers=6, port=4001, local_agents=False, ssh_target='runner@host-b',
                ssh_option=[], node_worker_threads=count, traffic_during_outage=True,
                publisher_outbound_queue=True)
            with patch('run_p2p_soak.Controller', return_value=controller), \
                 patch.object(controller, 'launch', side_effect=lambda host, command: launched.update({host: command})), \
                 patch.object(controller, 'wait', side_effect=RuntimeError('stop_after_launch')), \
                 patch.object(controller, 'cleanup'):
                with self.assertRaisesRegex(RuntimeError, 'stop_after_launch'):
                    measure(args)
            self.assertEqual(set(launched), {'host-a', 'host-b', 'host-c'})
            for host, command in launched.items():
                parts = command if host == 'host-a' else shlex.split(command[-1])
                with self.subTest(count=count, host=host):
                    self.assertIn('--publisher-outbound-queue', parts)
                    if count is None:
                        self.assertNotIn('--node-worker-threads', parts)
                    else:
                        self.assertEqual(parts[parts.index('--node-worker-threads') + 1], str(count))

    def test_outbound_queue_is_private_and_enabled_only_on_publishers(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch('run_p2p_soak.subprocess.Popen') as launch, \
             patch('run_p2p_soak.threading.Thread'), \
             patch.dict(os.environ, {'OSIRIS_P2P_OUTBOUND_DB': '/inherited.sqlite3'}):
            args = SimpleNamespace(binary=Path('/bin/true'), port=0, controller_pid=None,
                                   publisher_outbound_queue=True)
            agent = Agent(args, tmp)
            agent.start(0)
            self.assertNotIn('OSIRIS_P2P_OUTBOUND_DB', launch.call_args.kwargs['env'])
            agent.start(1, recipient='sink')
            env = launch.call_args.kwargs['env']
            database = Path(tmp) / '1' / 'outbound.sqlite3'
            self.assertEqual(Path(env['OSIRIS_P2P_OUTBOUND_DB']), database.resolve())
            self.assertEqual(database.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(env['OSIRIS_MAX_OBSERVATION_AGE_SECS'], '120')
            self.assertEqual(env['OSIRIS_TRUSTED_PEERS'], 'sink')

    def test_cli_rejects_unbounded_worker_settings_before_starting_agents(self):
        script = Path(__file__).with_name('run_p2p_soak.py')
        for invalid in ('0', '3', '8', 'true'):
            result = subprocess.run([sys.executable, str(script), '--agent', '--binary', '/bin/true',
                                     '--node-worker-threads', invalid], capture_output=True, text=True)
            with self.subTest(invalid=invalid):
                self.assertEqual(result.returncode, 2)
                self.assertIn('--node-worker-threads', result.stderr)

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
                            '{"pid":os.getpid(),"state":os.environ["OSIRIS_NODE_KEY"],"workers":os.environ.get("TOKIO_WORKER_THREADS")}))\n'
                            'print("peer_id=test-peer",flush=True)\n'
                            'while True: signal.pause()\n')
            node.chmod(0o700)
            agent = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name('run_p2p_soak.py')),
                 '--agent', '--binary', str(node), '--port', '19001', '--node-worker-threads', '1'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                agent.stdin.write('{"action":"sink"}\n')
                agent.stdin.flush()
                metadata = None
                while True:
                    event = json.loads(agent.stdout.readline())
                    if event['kind'] == 'metadata':
                        metadata = event
                    if event['kind'] == 'peer':
                        break
                child = json.loads(marker.read_text())
                self.assertEqual(child['workers'], '1')
                self.assertEqual(metadata['runtime_config']['node_worker_threads_requested'], 1)
                self.assertEqual(metadata['runtime_config']['tokio_worker_threads_env'], '1')
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
