#!/usr/bin/env python3
"""Measure real encrypted P2P traffic; Linux agents run on SSH-linked hosts.

No raw node logs or private state are exported. The controller measures round
trip-to-observer delivery latency on one monotonic clock, including SSH event
transport (a conservative upper bound, not synchronized host-clock latency).
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


def revision():
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    clean = not subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip()
    return commit, clean


def digest_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] if ordered else 0.0


def measurement_class(metadata, local_agents):
    separate = (len(metadata) >= 2 and
                len({m['host_fingerprint'] for m in metadata.values()}) == len(metadata))
    return ('operator_attested_real_multi_host' if separate and not local_agents
            else 'single_host_process_regression')


class Agent:
    def __init__(self, args, state):
        self.args, self.state = args, Path(state)
        self.nodes = {}
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.expected_stops = set()
        self.rss_peak = self.cpu_peak = self.disk_peak = 0
        self.previous_cpu = {}
        self.previous_time = time.monotonic()
        self.cpu_seconds = 0.0
        self.diagnostics = Counter()
        self.controllers = [os.getpid()]
        if args.controller_pid:
            self.controllers.append(args.controller_pid)

    def emit(self, kind, **data):
        with self.lock:
            print(json.dumps({'kind': kind, **data}, separators=(',', ':')), flush=True)

    def start(self, index, bootstrap='', recipient=''):
        directory = self.state / str(index)
        directory.mkdir(mode=0o700, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith('OSIRIS_')}
        env.update({
            'OSIRIS_NODE_KEY': str(directory / 'identity'),
            'OSIRIS_TRANSPORT_KEY': str(directory / 'transport'),
            'OSIRIS_PEER_KEY_STORE': str(directory / 'keys.sqlite3'),
            'OSIRIS_REPLAY_CACHE': str(directory / 'replay'),
            'OSIRIS_REVOCATION_REGISTRY': str(directory / 'revocations.json'),
            'OSIRIS_PEER_CACHE': str(directory / 'peer-cache.json'),
            'OSIRIS_E2EE_ENABLED': '1', 'OSIRIS_MDNS_ENABLED': '0',
            'OSIRIS_UPNP_ENABLED': '0', 'OSIRIS_HOLE_PUNCH_ENABLED': '0',
            'OSIRIS_HEALTH_HTTP_ADDR': '127.0.0.1:0',
            'OSIRIS_BOOTSTRAP_RECONNECT_INTERVAL_SECS': '5',
            'OSIRIS_LISTEN_ADDR': f'/ip4/0.0.0.0/tcp/{self.args.port if index == 0 else 0}',
        })
        if bootstrap:
            env['OSIRIS_BOOTSTRAP_PEERS'] = bootstrap
        if recipient:
            env['OSIRIS_E2EE_RECIPIENTS'] = recipient
            # The declared soak is a publisher/recipient star. Prevent DHT and
            # Identify discovery from silently turning it into a full mesh.
            env['OSIRIS_TRUSTED_PEERS'] = recipient
        proc = subprocess.Popen([str(self.args.binary)], env=env, cwd=directory,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.nodes[index] = proc
        self.expected_stops.discard(index)
        threading.Thread(target=self.read_node, args=(index, proc), daemon=True).start()

    def read_node(self, index, proc):
        for line in proc.stdout:
            for prefix in ('encrypted_observation_published', 'encrypted_observation_deferred',
                           'publish_rejected', 'publish_deferred', 'key_binding_rejected',
                           'encrypted_message_rejected', 'key_binding_accepted',
                           'key_binding_refreshed', 'key_binding_refresh_deferred'):
                if line.startswith(prefix + ' '):
                    reason = re.search(r'\breason=([a-z_]{1,64})(?:\s|$)', line)
                    with self.lock:
                        self.diagnostics[prefix + (':' + reason[1] if reason else '')] += 1
            if len(line) > 32768:
                self.emit('fatal', reason='oversized_node_output', node=index)
                break
            if line.startswith('peer_id='):
                self.emit('peer', node=index, peer=line.strip().split('=', 1)[1])
            elif line.startswith('listener_ready ') and index == 0:
                if f'/tcp/{self.args.port}/' in line:
                    self.emit('listener', node=index)
            elif line.startswith('key_binding_accepted '):
                match = re.search(r'peer=(\S+)', line)
                if match:
                    self.emit('binding', node=index, peer=match[1])
            elif line.startswith('gossipsub_subscribed ') and 'topic=osiris/e2ee/messages/1' in line:
                match = re.search(r'peer=(\S+)', line)
                if match:
                    self.emit('subscription', node=index, peer=match[1])
            elif line.startswith('peer_disconnected=') and line.rstrip().endswith('remaining=0'):
                self.emit('disconnected', node=index, peer=line.split('=', 1)[1].split()[0])
            elif line.startswith('encrypted_observation_received ') and index == 0:
                match = re.search(r'payload_sha256=([0-9a-f]{64})', line)
                if match:
                    self.emit('delivery', digest=match[1])
            elif line.startswith(('publish_rejected ', 'publish_deferred ')):
                # Rejections are diagnostic only: without a digest correlation,
                # they cannot subtract a message from lost/pending accounting.
                self.emit('publish_diagnostic', node=index)
        proc.wait()
        if (index not in self.expected_stops and not self.done.is_set()
                and self.nodes.get(index) is proc):
            self.emit('fatal', reason='node_exited', node=index, code=proc.returncode)

    def stop_node(self, index):
        self.expected_stops.add(index)
        proc = self.nodes[index]
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def sample(self):
        now = time.monotonic()
        pids = self.controllers + [p.pid for p in list(self.nodes.values()) if p.poll() is None]
        rss, cpu_delta = 0, 0.0
        for pid in pids:
            try:
                raw = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
                ticks = int(raw[11]) + int(raw[12])
                rss += int(raw[21]) * os.sysconf('SC_PAGE_SIZE')
                if pid in self.previous_cpu:
                    cpu_delta += (ticks - self.previous_cpu[pid]) / os.sysconf('SC_CLK_TCK')
                self.previous_cpu[pid] = ticks
            except (FileNotFoundError, ProcessLookupError):
                pass
        elapsed = now - self.previous_time
        self.cpu_seconds += cpu_delta
        cpu = 100 * cpu_delta / max(elapsed, .001) / (os.cpu_count() or 1)
        self.previous_time = now
        # Snapshot disk use before cleanup; never reset state during the run.
        disk = 0
        for path in self.state.rglob('*'):
            try:
                if path.is_file():
                    disk += path.stat().st_size
            except FileNotFoundError:  # SQLite may unlink a journal during sampling.
                pass
        self.rss_peak = max(self.rss_peak, rss)
        self.cpu_peak = max(self.cpu_peak, cpu)
        self.disk_peak = max(self.disk_peak, disk)
        return {'rss_peak_bytes': self.rss_peak, 'cpu_percent': self.cpu_peak,
                'disk_growth_bytes': self.disk_peak, 'cpu_seconds': self.cpu_seconds,
                'logical_cpus': os.cpu_count() or 1, 'sampling_interval_seconds': 1,
                'diagnostic_counts': dict(self.diagnostics)}

    def monitor(self):
        while not self.done.wait(1):
            try:
                self.emit('metrics', **self.sample())
            except Exception:
                self.emit('fatal', reason='resource_measurement_failed')
                return

    def run(self):
        commit, clean = revision()
        self.emit('metadata', commit_sha=commit, clean_worktree=clean,
                  binary_sha256=digest_file(self.args.binary),
                  host_fingerprint=digest_file('/proc/sys/kernel/random/boot_id'))
        def interrupted(_signum, _frame):
            raise SystemExit(1)
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGHUP, interrupted)
        threading.Thread(target=self.monitor, daemon=True).start()
        try:
            for line in sys.stdin:
                command = json.loads(line)
                action = command['action']
                if action == 'sink':
                    self.start(0)
                elif action == 'peers':
                    first = 1 if self.args.port else 0
                    for i in range(first, self.args.count):
                        self.start(i, command['bootstrap'], command['recipient'])
                        time.sleep(.1)
                    self.emit('started')
                elif action == 'publish':
                    proc = self.nodes[command['node']]
                    proc.stdin.write(json.dumps(command['observation'], separators=(',', ':')) + '\n')
                    proc.stdin.flush()
                elif action == 'stop_sink':
                    self.stop_node(0)
                    self.emit('sink_stopped')
                elif action == 'restart_sink':
                    self.start(0)
                elif action == 'finish':
                    self.emit('metrics', **self.sample())
                    self.emit('finished')
                    return
                else:
                    raise ValueError('unknown command')
        finally:
            self.done.set()
            for index in list(self.nodes):
                self.stop_node(index)


class Controller:
    def __init__(self):
        self.agents, self.metadata, self.peers, self.bindings, self.metrics = {}, {}, {}, set(), {}
        self.events = queue.Queue(maxsize=50000)
        self.failure = None
        self.stage = 'starting'
        self.deliveries, self.sent, self.samples = {}, {}, []
        self.sources = {}
        self.finished, self.listeners = set(), set()
        self.subscriptions = set()
        self.sink_stopped = False

    def launch(self, host, command):
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=None, text=True, bufsize=1, start_new_session=True)
        self.agents[host] = proc

        def read():
            try:
                for line in proc.stdout:
                    if len(line) > 32768:
                        raise ValueError('oversized_agent_event')
                    self.events.put((host, json.loads(line)), timeout=10)
            except Exception:
                self.failure = 'agent_event_stream_invalid'
            finally:
                self.events.put((host, {'kind': 'eof'}), timeout=10)
        threading.Thread(target=read, daemon=True).start()

    def command(self, host, **data):
        self.agents[host].stdin.write(json.dumps(data, separators=(',', ':')) + '\n')
        self.agents[host].stdin.flush()

    def drain(self, timeout=0):
        try:
            host, event = self.events.get(timeout=timeout)
        except queue.Empty:
            return
        kind = event['kind']
        if kind == 'metadata':
            self.metadata[host] = event
        elif kind == 'peer':
            previous = self.peers.get((host, event['node']))
            if previous and previous != event['peer']:
                raise RuntimeError('peer_identity_changed_on_restart')
            self.peers[(host, event['node'])] = event['peer']
        elif kind == 'listener':
            self.listeners.add(host)
        elif kind == 'binding':
            self.bindings.add((host, event['node'], event['peer']))
        elif kind == 'subscription':
            self.subscriptions.add((host, event['node'], event['peer']))
        elif kind == 'disconnected':
            self.subscriptions.discard((host, event['node'], event['peer']))
        elif kind == 'delivery':
            self.deliveries.setdefault(event['digest'], time.monotonic())
        elif kind == 'metrics':
            self.metrics[host] = event
        elif kind == 'finished':
            self.finished.add(host)
        elif kind == 'sink_stopped':
            self.sink_stopped = True
            self.subscriptions.clear()
        elif kind == 'fatal' or (kind == 'eof' and host not in self.finished):
            self.failure = f'{host}:{event.get("reason", "agent_ended_before_finish")}'
        if self.failure:
            raise RuntimeError(self.failure)

    def wait(self, condition, seconds=120, stage='readiness'):
        self.stage = stage
        deadline = time.monotonic() + seconds
        next_status = time.monotonic() + 10
        while not condition():
            if self.failure:
                raise RuntimeError(self.failure)
            if time.monotonic() >= deadline:
                raise TimeoutError(f'{stage}_timeout')
            if time.monotonic() >= next_status:
                print(f'soak_stage={stage} peers={len(self.peers)} bindings={len(self.bindings)}',
                      file=sys.stderr, flush=True)
                next_status = time.monotonic() + 10
            self.drain(.1)

    def publish(self, host, node, label):
        payload = label
        digest = hashlib.sha256(payload.encode()).hexdigest()
        if digest in self.sent:
            raise ValueError('duplicate_workload_id')
        self.sent[digest] = time.monotonic()
        self.sources[digest] = host
        self.command(host, action='publish', node=node, observation={
            'schema': 'osiris.observation.v1', 'instrument': 'OSIRIS-SOAK',
            'observed_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'provider': 'synthetic-soak', 'payload': payload, 'payload_sha256': digest,
        })
        return digest

    def sample_outage(self, start, delivered):
        elapsed = int((time.monotonic() - start) * 1000)
        if not self.samples or elapsed > self.samples[-1]['elapsed_ms']:
            self.samples.append({'elapsed_ms': elapsed, 'connected': delivered,
                                 'delivered': delivered})

    def observe_probes(self, pending, start, restart_after=None):
        recovered = False
        for digest in list(pending):
            if digest in self.deliveries:
                self.sample_outage(start, True)
                if restart_after is not None and self.sent[digest] >= restart_after:
                    recovered = True
                del pending[digest]
        return recovered

    def cleanup(self):
        for proc in self.agents.values():
            if proc.poll() is None:
                proc.stdin.close()  # Agent finally block stops every node.
        for proc in self.agents.values():
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, 15)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, 9)
                    proc.wait()


def measure(args):
    c = Controller()
    base = [sys.executable, str(Path(__file__).resolve()), '--agent', '--binary', str(args.binary)]
    extra_hosts = {}
    for specification in args.extra_host:
        host, target = specification.split('=', 1)
        if not re.fullmatch(r'host-[c-z]', host) or host in extra_hosts or not target:
            raise ValueError('invalid_extra_host')
        extra_hosts[host] = target
    host_count = 2 + len(extra_hosts)
    if host_count > args.peers:
        raise ValueError('more_hosts_than_peers')
    local_count = args.peers // host_count
    c.launch('host-a', base + ['--count', str(local_count), '--controller-pid', str(os.getpid())])
    remote_args = ['python3', args.remote_script, '--agent', '--binary', args.remote_binary,
                   '--count', str(args.peers - local_count * (host_count-1)), '--port', str(args.port)]
    if args.local_agents:
        remote_args = [sys.executable, str(Path(__file__).resolve()), *remote_args[2:]]
        c.launch('host-b', remote_args)
    else:
        c.launch('host-b', ['ssh', '-T', *args.ssh_option, args.ssh_target,
                            shlex.join(remote_args)])
    for host, target in extra_hosts.items():
        peer_args = ['python3', args.remote_script, '--agent', '--binary', args.remote_binary,
                     '--count', str(local_count)]
        c.launch(host, ['ssh', '-T', *args.ssh_option, target, shlex.join(peer_args)])
    try:
        c.wait(lambda: len(c.metadata) == host_count, stage='agent_metadata')
        commit, clean = revision()
        if any(m['commit_sha'] != commit or not m['clean_worktree'] for m in c.metadata.values()):
            if not args.local_agents:
                raise RuntimeError('agents_require_same_clean_candidate')
        c.command('host-b', action='sink')
        c.wait(lambda: ('host-b', 0) in c.peers and 'host-b' in c.listeners, stage='sink_listener')
        sink = c.peers[('host-b', 0)]
        bootstrap = f'/ip4/{args.host_b_ip}/tcp/{args.port}/p2p/{sink}'
        for host in c.agents:
            c.command(host, action='peers', bootstrap=bootstrap, recipient=sink)
        c.wait(lambda: len(c.peers) == args.peers, seconds=180, stage='peer_startup')
        if len(set(c.peers.values())) != args.peers:
            raise RuntimeError('duplicate_peer_identity')
        senders = [pair for pair in c.peers if pair != ('host-b', 0)]
        c.wait(lambda: all((h, n, sink) in c.bindings and
                          ('host-b', 0, c.peers[(h, n)]) in c.bindings for h, n in senders),
               180, stage='key_binding_exchange')
        # Require actual delivery from every publisher before starting the clock.
        run_id = os.urandom(12).hex()
        warmup = [c.publish(h, n, f'{run_id}-warmup-{i}') for i, (h, n) in enumerate(senders)]
        c.wait(lambda: all(d in c.deliveries for d in warmup), 90, stage='encrypted_warmup')
        print('All peers delivered encrypted warmup observations', file=sys.stderr, flush=True)
        started_at = datetime.now(timezone.utc)
        start = time.monotonic()
        workload, latencies = [], []
        outage_start = None
        restart_at = None
        probe_due = start
        probes = {}
        stopped = restarted = False
        restarted_time = None
        recovery_seen = False
        recovery_warmup = None
        next_message = start
        period = (args.duration - 10) / args.messages
        next_status = start
        c.stage = 'measurement'
        while time.monotonic() < start + args.duration or len(workload) < args.messages:
            now = time.monotonic()
            elapsed = now - start
            if not stopped and elapsed >= args.duration / 2:
                # This experiment declares a drained restart, not in-flight
                # durability. A real pre-outage delivery failure still fails.
                c.wait(lambda: all(d in c.deliveries for d in workload),
                       30, stage='pre_outage_drain')
                outage_start = int((time.monotonic() - start) * 1000)
                c.command('host-b', action='stop_sink')
                c.wait(lambda: c.sink_stopped, 15, stage='sink_stop')
                c.stage = 'outage_recovery'
                c.sample_outage(start, False)
                probes.clear()  # A pre-outage probe cannot establish recovery.
                stopped = True
                restart_at = time.monotonic() + 5
            if stopped and not restarted and now >= restart_at:
                c.command('host-b', action='restart_sink')
                restarted = True
                restarted_time = now
            # Pause scheduled workload during the outage; probes continue. This
            # measures recovery, not guaranteed in-flight message durability.
            if (stopped and recovery_seen and recovery_warmup is None
                    and all((h, n, sink) in c.subscriptions for h, n in senders)):
                recovery_warmup = [c.publish(h, n, f'{run_id}-recovery-warmup-{i}')
                                   for i, (h, n) in enumerate(senders)]
            all_recovered = (recovery_warmup is not None
                             and all(d in c.deliveries for d in recovery_warmup))
            paused = stopped and not all_recovered
            if now >= next_message and len(workload) < args.messages and not paused:
                h, n = senders[len(workload) % len(senders)]
                workload.append(c.publish(h, n, f'{run_id}-message-{len(workload)}'))
                next_message = now + period
            # Retain outstanding probes: an observation arriving after the next
            # one-second sampling tick still proves delivery/recovery.
            if c.observe_probes(probes, start, restarted_time if restarted else None):
                recovery_seen = True
                c.stage = 'measurement'
            if now >= probe_due and (not stopped or restarted):
                c.sample_outage(start, False)
                # Always crosses the host boundary (host-a -> host-b).
                probe = c.publish('host-a', 0, f'{run_id}-probe-{len(c.sent)}')
                probes[probe] = c.sent[probe]
                probe_due = now + 1
            if stopped and not all_recovered and (time.monotonic()-start)*1000 - outage_start > 60000:
                raise RuntimeError('all_publishers_recovery_not_observed_within_60_seconds')
            if now >= next_status:
                print(f'soak_elapsed={elapsed:.0f}s published={len(workload)}', file=sys.stderr, flush=True)
                next_status = now + 30
            c.drain(.005)
        # Explicit drain timeout; unsatisfied messages are lost, never silently
        # removed from the final accounting or rewritten with a new envelope.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not all(d in c.deliveries for d in workload):
            c.drain(.1)
        ended_at = datetime.now(timezone.utc)
        for host in c.agents:
            c.command(host, action='finish')
        c.wait(lambda: len(c.finished) == host_count, 30)
        latencies = [(c.deliveries[d]-c.sent[d])*1000 for d in workload if d in c.deliveries]
        host_records = []
        for host, metadata in c.metadata.items():
            metrics = c.metrics[host]
            host_records.append({'host_id': host, 'binary_sha256': metadata['binary_sha256'],
                                 **{k: metrics[k] for k in ('rss_peak_bytes', 'cpu_percent', 'disk_growth_bytes')}})
        independent = len({m['host_fingerprint'] for m in c.metadata.values()}) == host_count
        evidence = {
            'schema': 'osiris.p2p_load_acceptance.v1',
            'measurement_class': measurement_class(c.metadata, args.local_agents),
            'commit_sha': commit, 'clean_worktree': clean,
            'workflow_url': args.workflow_url, 'attempt': args.attempt, 'operator': 'github-actions',
            'started_at': started_at.isoformat(), 'ended_at': ended_at.isoformat(),
            'workload': {'peers': len(c.peers), 'messages': len(workload), 'fanout': 1,
                         'duration_seconds': (ended_at-started_at).total_seconds()},
            'delivery': {'sent': len(workload), 'delivered': len(latencies),
                         'lost': len(workload)-len(latencies), 'rejected': 0, 'pending': 0},
            'latency_ms': {'count': len(latencies), 'p50': percentile(latencies, .5),
                           'p95': percentile(latencies, .95), 'p99': percentile(latencies, .99),
                           'max': max(latencies, default=0)},
            'hosts': host_records, 'outage': {'kind': 'bootstrap', 'outage_start_ms': outage_start,
                                             'samples': c.samples},
            'delivery_records': [
                {'payload_sha256': d, 'outcome': 'delivered' if d in c.deliveries else 'lost',
                 'source_host': c.sources[d], 'recipient_host': 'host-b',
                 'latency_ms': (c.deliveries[d]-c.sent[d])*1000 if d in c.deliveries else None}
                for d in workload
            ],
            'measurement_notes': {
                'latency': 'controller monotonic write-to-observed-decryption, includes SSH observer transport',
                'resources': 'aggregate node and agent RSS; peak one-second CPU as share of host logical CPUs',
                'controller_resources_included_on_host_a': True,
                'workload_paused_during_outage': True, 'outage_probes_excluded_from_workload': True,
                'workload_drained_before_outage': True,
                'all_publishers_decryption_rechecked_before_resuming': True,
                'diagnostic_counts': {h: c.metrics[h]['diagnostic_counts'] for h in c.metrics},
                'state_preserved_across_sink_restart': True,
                'topology': 'recipient-bootstrap star; publishers trust only recipient',
                'open_mesh_capacity_tested': False,
                'rss_is_sampled_not_continuous': True,
                'independent_host_fingerprints_distinct': independent,
            },
        }
        return evidence
    except Exception:
        args.partial = {
            'stage': c.stage,
            'metadata': c.metadata, 'host_metrics': c.metrics,
            'observed_peer_count': len(c.peers), 'known_delivery_count': len(c.deliveries),
            'submitted_including_warmup_and_probes': len(c.sent),
            'outage_samples': c.samples,
        }
        raise
    finally:
        c.cleanup()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--agent', action='store_true')
    p.add_argument('--binary', type=Path, required=True)
    p.add_argument('--count', type=int, default=2)
    p.add_argument('--controller-pid', type=int)
    p.add_argument('--port', type=int, default=0)
    p.add_argument('--ssh-target')
    p.add_argument('--extra-host', action='append', default=[],
                   help='additional publisher host, e.g. host-c=runner@100.64.0.3')
    p.add_argument('--ssh-option', action='append', default=[])
    p.add_argument('--remote-binary')
    p.add_argument('--remote-script', default=str(Path(__file__).resolve()))
    p.add_argument('--host-b-ip', default='127.0.0.1')
    p.add_argument('--peers', type=int, default=100)
    p.add_argument('--messages', type=int, default=10000)
    p.add_argument('--duration', type=int, default=900)
    p.add_argument('--local-agents', action='store_true')
    p.add_argument('--workflow-url', default='https://github.com/Osiris6900/osiris-eye/actions')
    p.add_argument('--attempt', type=int, default=1)
    p.add_argument('--evidence', type=Path)
    args = p.parse_args()
    args.binary = args.binary.resolve()
    if not args.binary.is_file():
        p.error('binary must exist')
    if args.agent:
        if not 1 <= args.count <= 200:
            p.error('count must be between 1 and 200')
        with tempfile.TemporaryDirectory(prefix='osiris-soak-agent-') as state:
            Agent(args, state).run()
        return 0
    if not (4 <= args.peers <= 400 and 1 <= args.messages <= 100000 and 20 <= args.duration <= 3600):
        p.error('bounded workload required: 4..400 peers, 1..100000 messages, 20..3600 seconds')
    if not args.evidence or not 1 <= args.port <= 65535 or (not args.local_agents and not args.ssh_target):
        p.error('evidence, sink TCP port and SSH target required (or local-agents for smoke only)')
    args.remote_binary = args.remote_binary or str(args.binary)
    started = datetime.now(timezone.utc).isoformat()
    try:
        evidence = measure(args)
    except Exception as exc:
        evidence = {'schema': 'osiris.p2p_soak_failure.v1', 'status': 'fail',
                    'started_at': started, 'ended_at': datetime.now(timezone.utc).isoformat(),
                    'reason': type(exc).__name__ + ':' + str(exc),
                    'partial_measurements': getattr(args, 'partial', {})}
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(evidence, indent=2)+'\n')
        print(json.dumps(evidence), file=sys.stderr)
        return 1
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, indent=2)+'\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
