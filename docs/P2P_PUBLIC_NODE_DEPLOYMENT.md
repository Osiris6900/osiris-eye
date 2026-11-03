# Public P2P Node Deployment Guide

This guide prepares an operator to run OSIRIS P2P on an independently reachable Linux host. The process-level CI tests use localhost; they do **not** prove public Internet reachability, NAT traversal across arbitrary networks, or production availability.

## 1. Build

Install a current stable Rust toolchain, then build the P2P binaries from the repository root:

```bash
cargo build --release --manifest-path services/p2p-node/Cargo.toml --bins
```

Keep the resulting binaries and their configuration under an operator-controlled service account.

## 2. Stable node identity

Each node must keep its own `OSIRIS_NODE_KEY` file across restarts. Back it up securely and never share it between nodes. The application creates the file on first start and applies mode `0600` on Unix. Losing the key changes the Peer ID; exposing it lets another operator impersonate that node.

Example:

```bash
install -d -m 0700 /var/lib/osiris-p2p
export OSIRIS_NODE_KEY=/var/lib/osiris-p2p/node.key
```

## 3. Listen and advertise a reachable address

Set a stable TCP listen port and advertise the public IP or DNS address that actually forwards to that port:

```bash
export OSIRIS_LISTEN_ADDR=/ip4/0.0.0.0/tcp/4001
export OSIRIS_EXTERNAL_ADDRS=/ip4/203.0.113.10/tcp/4001
./target/release/osiris-p2p-node
```

Replace the documentation-only IP above with the host's real public address. For DNS, use a TCP multiaddress such as `/dns4/p2p.example.net/tcp/4001`. Multiple addresses can be comma-separated.

`OSIRIS_EXTERNAL_ADDRS` accepts TCP multiaddresses without a trailing `/p2p/<peer-id>`. The node advertises them to libp2p peers. The operator must ensure that the address is correct and that the same TCP port is forwarded to the process. Incorrect advertised addresses make a node appear reachable when it is not.

## 4. Firewall and network

- Permit inbound TCP on the configured P2P port (for example, 4001) at both the host firewall and cloud security-group layers.
- Permit outbound TCP so the node can dial peers.
- If the host is behind NAT, configure port forwarding to the node and advertise the public-side address.
- The current transport is TCP with Noise authentication and Yamux multiplexing. Do not assume UDP/QUIC support.
- Keep SSH/admin access restricted separately; do not expose management services on the P2P port.

## 5. Bootstrap configuration

A node that should join an existing network needs at least one reachable bootstrap multiaddress, including that node's Peer ID:

```bash
export OSIRIS_BOOTSTRAP_PEERS=/ip4/203.0.113.10/tcp/4001/p2p/REPLACE_WITH_BOOTSTRAP_PEER_ID
```

For resilience, configure more than one independently hosted bootstrap node, comma-separated. The bootstrap nodes must themselves advertise reachable addresses. mDNS discovery is useful on a local network; it is not a substitute for bootstrap configuration across the public Internet.

### Bootstrap reconnect policy

Nodes retry disconnected configured bootstrap peers periodically. The default retry interval is 15 seconds; operators can tune it between 5 and 300 seconds:

```bash
export OSIRIS_BOOTSTRAP_RECONNECT_INTERVAL_SECS=15
```

Invalid values fall back to the 15-second default, and values outside the supported range are clamped. Keep the interval conservative on large deployments so an outage does not create unnecessary dial pressure. This setting controls retry cadence; it does not guarantee that a peer is reachable or that a connection will succeed.

### Optional core-node peer allowlist

Each core P2P node can independently restrict authenticated connections and observation traffic to an operator-maintained Peer ID set:

```bash
export OSIRIS_TRUSTED_PEERS=12D3KooW...BOOTSTRAP_PEER,12D3KooW...PUBLISHER_PEER
```

Use real Peer IDs, comma-separated, with no placeholder values. When this variable is unset, the node remains in open-network mode. When set, it must contain at least one valid Peer ID and include every configured `OSIRIS_BOOTSTRAP_PEERS` Peer ID; otherwise startup fails. Unknown mDNS peers are not dialed, unknown inbound peers are disconnected, and messages from untrusted propagation sources are rejected.

This is a per-node admission policy, not a network-wide identity registry, certificate authority, revocation service, or protection against a compromised trusted key. Every participating node must be configured consistently by its operator. Keep the allowlist current as peers are added or revoked.

## 6. Circuit Relay

A relay is a separate process and should run on a host with a stable, publicly reachable TCP address. Relay service is opt-in:

```bash
export OSIRIS_RELAY_SERVER_ENABLED=true
export OSIRIS_RELAY_NODE_KEY=/var/lib/osiris-p2p/relay.key
export OSIRIS_RELAY_LISTEN_ADDR=/ip4/0.0.0.0/tcp/4001
./target/release/osiris-p2p-relay
```

Keep relay keys private. Review the built-in reservation/circuit limits before exposing a relay publicly. Core-node and relay-client allowlists are independently configured per process; they are not a network-wide authorization system, identity registry, or centrally enforced server-side ACL.

## 7. Node health endpoint and heartbeat

The node exposes a small HTTP liveness endpoint and continues to emit a machine-readable `node_health` log line.

By default, the endpoint binds only to loopback at `127.0.0.1:9090`. Configure the address explicitly when a local monitoring agent needs a different interface or port:

```bash
export OSIRIS_HEALTH_HTTP_ADDR=127.0.0.1:9090
export OSIRIS_HEALTH_INTERVAL_SECS=30
```

The interval is clamped to 5–300 seconds. Query:

```bash
curl http://127.0.0.1:9090/health
```

Example JSON:

```json
{
  "status": "alive",
  "peer_id": "12D3KooW...",
  "uptime_seconds": 120,
  "connected_peers": 4,
  "bootstrap_peers_connected": 2,
  "bootstrap_peers_total": 2
}
```

The same counters are emitted periodically in the `node_health` log line. A zero peer count is not automatically a process failure; a new or isolated node may be running without peers. Alert separately when expected bootstrap peers remain disconnected.

This endpoint is a liveness snapshot, not a readiness guarantee: it does not prove that a remote peer can reach this node, that DHT queries succeed, or that observations are being delivered. Keep it on loopback unless network exposure is intentional and protected by your deployment's access controls. Use a service manager (for example systemd) to restart crashed processes and collect logs.

## 8. Operator checklist before public launch

- [ ] Two or more independent hosts/regions are provisioned.
- [ ] Each node has its own persistent key and Peer ID recorded securely.
- [ ] Public TCP ports are reachable from outside each provider network.
- [ ] Advertised external addresses match real public routing and port forwarding.
- [ ] Bootstrap peer addresses are configured on joining nodes.
- [ ] Logs, process supervision, restart policy, disk usage, and host health are monitored.
- [ ] Relay limits, abuse reporting, and an operator response process are defined.
- [ ] A cross-host acceptance test exchanges a signed observation between machines on different networks.

## Current limitations

This is deployment guidance, not a deployment or security certification. The node currently uses an in-memory Kademlia store and does not provide a centrally managed bootstrap registry. The HTTP health endpoint is a local liveness/readiness snapshot, not proof of end-to-end message delivery. The relay/client allowlist does not enforce network-wide trust. Public launch should follow cross-host testing, monitoring, security review, and operational ownership.
