#!/usr/bin/env python3
"""Exercise the shipped profile on a fresh disposable GitHub-hosted runner.

Never run this on an operator's server. Existing users, units, binaries or state
cause refusal before any mutation. Artifacts contain public results only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import stat
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = 'osiris-p2p'
UNIT = 'osiris-p2p.service'
STATE = Path('/var/lib/osiris-p2p')
CONFIG = Path('/etc/osiris-p2p')
LIBEXEC = Path('/usr/local/libexec/osiris-p2p')
DOC = Path('/usr/local/share/doc/osiris-p2p')
INSTALLED = Path('/usr/local/bin/osiris-p2p-node')
UNIT_PATH = Path('/etc/systemd/system') / UNIT
# RFC 8032 test-vector PUBLIC verification key; no signing key is installed.
AUTHORITY_PUBLIC = 'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a'


def require_disposable(env, uid, paths, account_exists, group_exists):
    if (uid != 0 or env.get('GITHUB_ACTIONS') != 'true'
            or env.get('RUNNER_ENVIRONMENT') != 'github-hosted'):
        raise RuntimeError('requires root on a disposable GitHub-hosted runner')
    if account_exists or group_exists or any(p.exists() or p.is_symlink() for p in paths):
        raise RuntimeError('refusing to overwrite existing account or deployment')


def exists(database, name):
    try:
        database(name)
        return True
    except KeyError:
        return False


def run(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True, timeout=30)


def wait_for(predicate, description, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.25)
    raise RuntimeError('timeout: ' + description)


def health(port, ready=False):
    try:
        with urlopen(f'http://127.0.0.1:{port}/' + ('ready' if ready else 'health'), timeout=1) as r:
            value = json.load(r)
        return value if value.get('status') == 'alive' and (not ready or value.get('ready') is True) else None
    except (URLError, OSError, ValueError):
        return None


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main_pid():
    value = run('systemctl', 'show', UNIT, '--property=MainPID', '--value').stdout.strip()
    return int(value or 0)


def verify(binary, checks):
    protected = [STATE, CONFIG, LIBEXEC, DOC, INSTALLED, UNIT_PATH]
    require_disposable(os.environ, os.geteuid(), protected,
                       exists(pwd.getpwnam, ACCOUNT), exists(grp.getgrnam, ACCOUNT))
    if not Path('/run/systemd/system').is_dir():
        raise RuntimeError('systemd must be PID 1 on this disposable runner')
    bootstrap = None
    account_created = False
    try:
        run('useradd', '--system', '--user-group', '--no-create-home', '--home-dir', str(STATE),
            '--shell', '/usr/sbin/nologin', ACCOUNT)
        account_created = True
        account = pwd.getpwnam(ACCOUNT)
        CONFIG.mkdir(mode=0o700)
        LIBEXEC.mkdir(mode=0o755, parents=True)
        DOC.mkdir(mode=0o755, parents=True)
        shutil.copyfile(ROOT / 'docs/P2P_HARDENED_DEPLOYMENT.md', DOC / 'P2P_HARDENED_DEPLOYMENT.md')
        shutil.copyfile(ROOT / 'deploy/p2p/preflight.py', LIBEXEC / 'preflight.py')
        (LIBEXEC / 'preflight.py').chmod(0o644)
        shutil.copyfile(binary, INSTALLED)
        INSTALLED.chmod(0o755)
        shutil.copyfile(ROOT / 'deploy/p2p/osiris-p2p.service', UNIT_PATH)
        UNIT_PATH.chmod(0o644)
        with tempfile.TemporaryDirectory(prefix='osiris-systemd-bootstrap-') as directory:
            temp = Path(directory)
            os.chown(temp, account.pw_uid, account.pw_gid)
            ports = set()
            while len(ports) < 3:
                ports.add(free_port())
            bootstrap_port, bootstrap_health, service_health = ports
            env = {k: v for k, v in os.environ.items() if not k.startswith('OSIRIS_')}
            env.update({
                'OSIRIS_NODE_KEY': str(temp / 'identity'),
                'OSIRIS_TRANSPORT_KEY': str(temp / 'transport'),
                'OSIRIS_PEER_KEY_STORE': str(temp / 'keys.sqlite3'),
                'OSIRIS_REPLAY_CACHE': str(temp / 'replay'),
                'OSIRIS_PEER_CACHE': str(temp / 'peer-cache.sqlite3'),
                'OSIRIS_E2EE_ENABLED': '1', 'OSIRIS_MDNS_ENABLED': '0',
                'OSIRIS_UPNP_ENABLED': '0', 'OSIRIS_AUTO_RELAY': '0',
                'OSIRIS_BOOTSTRAP_SEED_URL': 'off',
                'OSIRIS_LISTEN_ADDR': f'/ip4/127.0.0.1/tcp/{bootstrap_port}',
                'OSIRIS_HEALTH_HTTP_ADDR': f'127.0.0.1:{bootstrap_health}',
                'OSIRIS_HEALTH_INTERVAL_SECS': '5',
            })
            with (temp / 'bootstrap.log').open('w') as log:
                bootstrap = subprocess.Popen([str(INSTALLED)], cwd=temp, env=env, stdin=subprocess.DEVNULL,
                                             stdout=log, stderr=subprocess.STDOUT,
                                             user=account.pw_uid, group=account.pw_gid)
                peer = wait_for(lambda: health(bootstrap_health), 'bootstrap liveness')['peer_id']
                settings = {
                    'OSIRIS_E2EE_ENABLED': '1', 'OSIRIS_MDNS_ENABLED': '0',
                    'OSIRIS_NODE_KEY': str(STATE / 'identity.key'),
                    'OSIRIS_TRANSPORT_KEY': str(STATE / 'transport.key'),
                    'OSIRIS_PEER_KEY_STORE': str(STATE / 'peer-keys.sqlite3'),
                    'OSIRIS_REPLAY_CACHE': str(STATE / 'replay.cache'),
                    'OSIRIS_PEER_CACHE': str(STATE / 'peer-cache.sqlite3'),
                    'OSIRIS_REVOCATION_REGISTRY': str(STATE / 'revocations.json'),
                    'OSIRIS_REVOCATION_AUTHORITY_PUBLIC_KEY': AUTHORITY_PUBLIC,
                    'OSIRIS_TRANSPORT_KEY_EPOCH': '1', 'OSIRIS_MAX_OBSERVATION_AGE_SECS': '300',
                    'OSIRIS_TRUSTED_PEERS': peer, 'OSIRIS_E2EE_RECIPIENTS': peer,
                    'OSIRIS_BOOTSTRAP_PEERS': f'/ip4/127.0.0.1/tcp/{bootstrap_port}/p2p/{peer}',
                    'OSIRIS_LISTEN_ADDR': '/ip4/127.0.0.1/tcp/0',
                    'OSIRIS_HEALTH_HTTP_ADDR': f'127.0.0.1:{service_health}',
                    'OSIRIS_HEALTH_INTERVAL_SECS': '5', 'OSIRIS_UPNP_ENABLED': '0',
                    'OSIRIS_AUTO_RELAY': '0', 'OSIRIS_BOOTSTRAP_SEED_URL': 'off',
                }
                def write_env(values):
                    path = CONFIG / 'node.env'
                    path.write_text(''.join(f'{k}={v}\n' for k, v in values.items()))
                    path.chmod(0o600)
                write_env(settings)
                run('systemd-analyze', 'verify', str(UNIT_PATH))
                checks['unit_syntax'] = True
                run('systemctl', 'daemon-reload')
                run('systemctl', 'start', UNIT)
                initial = wait_for(lambda: health(service_health, True), 'service readiness')
                first_pid = main_pid()
                identity = (STATE / 'identity.key').read_bytes()
                transport = (STATE / 'transport.key').read_bytes()
                process_uid = int(Path(f'/proc/{first_pid}/status').read_text().split('Uid:')[1].split()[0])
                if process_uid != account.pw_uid or process_uid == 0:
                    raise RuntimeError('service did not drop privileges')
                checks['unprivileged_service_uid'] = True
                if stat.S_IMODE(STATE.stat().st_mode) != 0o700 or STATE.stat().st_uid != account.pw_uid:
                    raise RuntimeError('state directory privacy/ownership mismatch')
                for name in ('identity.key', 'transport.key'):
                    if stat.S_IMODE((STATE / name).stat().st_mode) != 0o600:
                        raise RuntimeError('private key permission mismatch')
                checks['private_state_permissions'] = True
                checks['loopback_health_and_bootstrap_readiness'] = True
                for label, signal_name in (('crash_restart', 'SIGKILL'), ('clean_exit_restart', 'SIGINT')):
                    old_pid = main_pid()
                    run('systemctl', 'kill', '--kill-whom=main', '--signal=' + signal_name, UNIT)
                    def restarted():
                        pid = main_pid()
                        if pid and pid != old_pid:
                            return health(service_health, True)
                        return None
                    current = wait_for(restarted, label)
                    if current['peer_id'] != initial['peer_id']:
                        raise RuntimeError('identity changed after ' + label)
                    if ((STATE / 'identity.key').read_bytes() != identity
                            or (STATE / 'transport.key').read_bytes() != transport):
                        raise RuntimeError('private state changed after ' + label)
                    checks[label] = True
                checks['durable_identity_and_transport_key'] = True
                # Explicit stop must remain stopped, despite Restart=always.
                run('systemctl', 'stop', UNIT)
                if main_pid() != 0:
                    raise RuntimeError('explicit stop left a running process')
                checks['explicit_stop'] = True
                write_env(dict(settings, OSIRIS_E2EE_ENABLED='0'))
                run('systemctl', 'reset-failed', UNIT, check=False)
                rejected = run('systemctl', 'start', UNIT, check=False)
                if rejected.returncode == 0 or main_pid() != 0:
                    raise RuntimeError('unsafe plaintext configuration was not rejected')
                preflight_result = run('systemctl', 'show', UNIT, '--property=ExecStartPre', '--value').stdout
                if 'code=exited' not in preflight_result or 'status=1' not in preflight_result:
                    raise RuntimeError('configuration rejection was not confirmed by preflight exit 1')
                checks['unsafe_configuration_rejected_before_node_start'] = True
    finally:
        if account_created:
            run('systemctl', 'stop', UNIT, check=False)
        if bootstrap is not None:
            bootstrap.terminate()
            try:
                bootstrap.wait(timeout=10)
            except subprocess.TimeoutExpired:
                bootstrap.kill()
                bootstrap.wait(timeout=5)
        # Only these fresh, preflight-guarded paths/account were created by this test.
        if account_created:
            for path in (UNIT_PATH, INSTALLED):
                path.unlink(missing_ok=True)
            for path in (CONFIG, LIBEXEC, DOC, STATE):
                if path.exists():
                    shutil.rmtree(path)
            run('systemctl', 'daemon-reload', check=False)
            run('systemctl', 'reset-failed', UNIT, check=False)
            run('userdel', ACCOUNT, check=False)
            if exists(grp.getgrnam, ACCOUNT):
                run('groupdel', ACCOUNT, check=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary', type=Path, required=True)
    p.add_argument('--evidence', type=Path, required=True)
    args = p.parse_args()
    evidence = {'schema': 'osiris.systemd_profile_smoke.v1', 'scope': 'single-disposable-host',
                'status': 'fail', 'started_at': datetime.now(timezone.utc).isoformat(), 'checks': {},
                'public_deployment_verified': False, 'long_term_uptime_verified': False}
    code = 0
    try:
        binary = args.binary.resolve(strict=True)
        evidence['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
        evidence['unit_sha256'] = hashlib.sha256((ROOT / 'deploy/p2p/osiris-p2p.service').read_bytes()).hexdigest()
        evidence['commit_sha'] = run('git', '-C', str(ROOT), 'rev-parse', 'HEAD').stdout.strip()
        verify(binary, evidence['checks'])
        evidence['status'] = 'pass'
    except Exception as error:
        # No environment values, key material, subprocess output or journals in artifacts.
        evidence['error'] = type(error).__name__ + ':' + str(error)
        code = 1
    evidence['finished_at'] = datetime.now(timezone.utc).isoformat()
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence, sort_keys=True))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
