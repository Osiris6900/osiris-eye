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

## 6. Circuit Relay

A relay is a separate process and should run on a host with a stable, publicly reachable TCP address. Relay service is opt-in:

```bash
export OSIRIS_RELAY_SERVER_ENABLED=true
export OSIRIS_RELAY_NODE_KEY=/var/lib/osiris-p2p/relay.key
export OSIRIS_RELAY_LISTEN_ADDR=/ip4/0.0.0.0/tcp/4001
./target/release/osiris-p2p-relay
```

Keep relay keys private. Review the built-in reservation/circuit limits before exposing a relay publicly. The current client allowlist is client-side configuration; it is not a network-wide authorization system or server-side ACL.

## 7. Node health heartbeat

The node emits a machine-readable `node_health` log line periodically. This is a lightweight liveness/peer-count signal for a process supervisor or log collector; it is not an HTTP health endpoint and does not prove that remote peers can reach the node.

The default interval is 30 seconds. Configure it between 5 and 300 seconds:

```bash
export OSIRIS_HEALTH_INTERVAL_SECS=30
```

Example log:

```text
node_health status=alive peer_id=... uptime_seconds=120 connected_peers=4 bootstrap_peers_connected=2/2
```

- `status=alive` means the event loop is running and emitted a heartbeat.
- `connected_peers` is the current number of connected libp2p peers.
- `bootstrap_peers_connected` reports connected configured bootstrap peers versus the configured total.
- A zero peer count is not automatically a process failure; a new or isolated node may be running without peers. Alert separately when expected bootstrap peers remain disconnected.

Use a service manager (for example systemd) to restart a crashed process and collect stdout/stderr logs. Do not treat this heartbeat alone as a readiness or end-to-end network check.

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

This is deployment guidance, not a deployment or security certification. The node currently uses an in-memory Kademlia store, has no operator HTTP health endpoint, and does not provide a centrally managed bootstrap registry. The relay/client allowlist does not enforce network-wide trust. Public launch should follow cross-host testing, monitoring, security review, and operational ownership.
