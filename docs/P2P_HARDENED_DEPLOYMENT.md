# Hardened observation receiver profile

`deploy/p2p` contains an optional Linux systemd deployment profile for an
operator-managed encrypted market-data node. Its scope is observation reception
for research and paper trading. Peer messages do not become orders, and this
profile does not connect a broker, execute trades, or bridge received data into
the paper simulator automatically.

The service uses a dedicated unprivileged account, private persistent state,
local-only health, explicit peer admission, recipient E2EE, signed revocation
authority configuration, and a 300-second observation freshness limit. Its
preflight rejects example placeholders, plaintext mode, open admission, public
health binding, recipient/trust mismatches, and identity rotation on restart.
The service permits `AF_NETLINK` so libp2p can discover local interface addresses
for wildcard TCP listeners; it does not grant `CAP_NET_ADMIN`.
The Rust node still validates PeerIds, multiaddrs, signatures and key bindings.
Preflight success is not proof that a remote peer is reachable or trustworthy.

`OSIRIS_MAX_CONNECTIONS_PER_PEER` optionally lowers the existing per-peer cap
from four to one, two or three; invalid values fail startup. A direct-only star
can use `1` to conserve slots. Parallel relay/direct paths or hole punching need
an appropriate multi-connection allowance. Directional and total caps stay fixed.
Explicit allow/block policy is enforced by libp2p connection admission as well as
application checks, so internal discovery cannot dial known non-allowlisted
PeerIds or establish handlers for untrusted authenticated peers.

## Install and configure

Use a Linux host with systemd 247 or newer and Python 3.10 or newer. Build and
test the binary from a reviewed commit on a compatible build machine. Install
the executable as `/usr/local/bin/osiris-p2p-node`, owned by root and not writable
by the service account. Record its commit and SHA-256 in your deployment record.

From a checkout of that same commit, an operator can install the profile with:

```bash
sudo useradd --system --home-dir /var/lib/osiris-p2p --shell /usr/sbin/nologin osiris-p2p
sudo install -d -o root -g root -m 0755 /usr/local/libexec/osiris-p2p
sudo install -o root -g root -m 0644 deploy/p2p/preflight.py /usr/local/libexec/osiris-p2p/preflight.py
sudo install -d -o root -g root -m 0700 /etc/osiris-p2p
sudo install -o root -g root -m 0600 deploy/p2p/node.env.example /etc/osiris-p2p/node.env
sudo install -o root -g root -m 0644 deploy/p2p/osiris-p2p.service /etc/systemd/system/osiris-p2p.service
```

If the account already exists, verify its ownership and privileges rather than
recreating it. Replace every `REPLACE_` value in the installed environment file.
Exchange the application peers' public PeerIds and the revocation authority's
**public** Ed25519 key over an authenticated operator channel. Add every intended
recipient and configured bootstrap/relay PeerId to `OSIRIS_TRUSTED_PEERS`.
Do not put the authority's signing private key on observation nodes.

Provision the node's persistent identity and obtain its public PeerId using the
[public-node setup guide](P2P_PUBLIC_NODE_DEPLOYMENT.md) before enrolling it on
other peers. Use the exact state paths from the environment file; never give
multiple active nodes the same identity or state directory. Local identity and
transport private keys stay under `/var/lib/osiris-p2p`, which systemd creates
with mode `0700`. An existing state directory must belong to `osiris-p2p` and
have mode `0700` before starting the service. Keep private backups separately
and follow the [rotation runbook](P2P_KEY_ROTATION_REVOCATION_RUNBOOK.md).

Allow inbound TCP 4001 only from the intended network or peers when direct
inbound connections are required. Keep health port 9091 on loopback. A public
bootstrap or relay must be independently operated and reachable. For an explicit
relay reservation, set `OSIRIS_RELAY_ADDRESS` to an IP TCP relay multiaddr; a
circuit bootstrap ends with `/p2p/RELAY/p2p-circuit/p2p/RECIPIENT`. Include both
relay and recipient in the allowlist. This is explicit circuit configuration,
not automatic NAT traversal or relay selection.

Review the installed configuration and unit, then start it:

```bash
sudo systemd-analyze verify /etc/systemd/system/osiris-p2p.service
sudo systemctl daemon-reload
sudo systemctl enable --now osiris-p2p.service
curl --fail http://127.0.0.1:9091/health
curl --fail http://127.0.0.1:9091/ready
sudo journalctl -u osiris-p2p.service -n 100 --no-pager
```

`/health` means the process is alive. `/ready` checks configured bootstrap and
relay connectivity; it does not certify feed freshness, licensing, data truth,
paper strategy validity or end-to-end recipient key readiness. Confirm an exact
synthetic payload digest on the intended recipient before admitting any feed.
Use licensed or otherwise authorized non-sensitive observations only.

## Operation and limits

- The supplied systemd unit has `/dev/null` stdin and runs as a receiver. The
  node's current publication interface is stdin JSON; a supervised local feed
  adapter and its failure semantics require separate integration. Do not assume
  the unit exposes a publication API.
- Observe rejection counters/logs, disk usage, peer connectivity and freshness.
  Maintain clock synchronization. This profile rejects observations over 300
  seconds old; historical research requires a separate deliberately configured
  process, not weakening the receiving node's gate.
- Replay protection is node-local, persistent and bounded. Capacity exhaustion
  fails closed; do not delete replay state to restore availability. Preserve
  accepted-message history and investigate before a planned state migration.
- Stop the service before backup, identity rotation or state maintenance.
  Back up identity, transport key, peer-key epoch floors, replay state and signed
  revocations consistently. Private state and SSH keys must never enter GitHub
  Actions artifacts or source control.
- Resource limits are initial operator defaults, not throughput guarantees.
  Monitor real workload and choose limits after measured load/soak testing.
- The existing recipient E2EE construction has no ratchet or forward-secrecy
  guarantee against later compromise of the recipient's transport key. Routing
  metadata remains visible. No independent cryptographic audit is established.
- A successful local core-relay test is distinct from cross-host and arbitrary
  public-network acceptance. Require retained passing evidence for the reviewed
  commit before public rollout. This profile does not establish such evidence.

Validate profile behavior locally with `python deploy/p2p/test_preflight.py`.
No deployment, public endpoint or live trading is enabled by adding these files.

## Disposable-runner operational smoke

`scripts/test_p2p_systemd_profile.py` exercises the shipped unit on a fresh
GitHub-hosted systemd runner. It refuses non-root, self-hosted and existing
installation cases before mutation. Checks cover privilege drop, private state,
loopback readiness, automatic restart after SIGKILL and clean SIGINT, persistent
identity/transport keys, explicit stop and fail-closed preflight. Temporary
accounts, installed test files and node state are cleaned afterwards.

Only sanitized checks, source/binary/unit hashes and timestamps enter artifacts.
A passing result covers one disposable host, not public reachability or uptime.
