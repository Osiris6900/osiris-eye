# OSIRIS EYE P2P two-machine test runbook

## Purpose and limits

This runbook is for an operator-supervised connectivity test between two machines. It validates encrypted libp2p transport and signed observation-envelope exchange. It does **not** validate market-data truth, production availability, NAT traversal, or trading execution. Do not put broker credentials, API secrets, orders, or account data in P2P messages.

The automated CI integration test uses two nodes on localhost. This runbook is the next manual step: two separately administered machines on a network where the listening TCP port is reachable.

## Prerequisites

- Rust toolchain compatible with the workspace and Git.
- Both machines can clone the private `Osiris6900/osiris-eye` repository.
- Machine A has a reachable TCP address from machine B. For the first test, use the same LAN and a private LAN IP; do not expose a node directly to the public Internet.
- Firewall permits the chosen TCP port from machine B to machine A only.
- Use a dedicated test environment and non-sensitive observation data.

## 1. Build on both machines

From the repository root on each machine:

```bash
cargo build --release --manifest-path services/p2p-node/Cargo.toml
```

## 2. Start node A (listener)

Choose an unused TCP port, for example 4001, and set the listen address to the machine A LAN interface:

```bash
export OSIRIS_LISTEN_ADDR=/ip4/0.0.0.0/tcp/4001
cargo run --release --manifest-path services/p2p-node/Cargo.toml
```

Record the actual listening multiaddress and PeerId printed by the node. If the process does not print a reachable multiaddress/PeerId, stop and inspect its startup output rather than guessing either value.

Keep node A running. Restrict the firewall rule for TCP 4001 to machine B's LAN IP.

## 3. Start node B and dial node A

On machine B, use the exact reachable LAN IP and PeerId recorded from node A:

```bash
export OSIRIS_BOOTSTRAP_PEERS='/ip4/<MACHINE_A_LAN_IP>/tcp/4001/p2p/<MACHINE_A_PEER_ID>'
cargo run --release --manifest-path services/p2p-node/Cargo.toml
```

Replace both placeholders with observed values. Do not use a guessed PeerId. Confirm from logs that node B establishes a connection to node A.

## 4. Publish a harmless test observation

Use the node's stdin JSON interface to publish a schema-valid observation envelope. Example shape (replace the timestamp and hash with test values):

```json
{"schema":"osiris.observation.v1","instrument":"TEST-PAIR","observed_at":"2026-11-03T00:00:00Z","provider":"manual-connectivity-test","payload_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
```

The current envelope validator checks required field formats and lengths. The current node sends observation metadata/hash, not the underlying market-data payload; it does not prove that the hash matches separately stored bytes or that the observation is accurate.

## 5. Pass criteria and evidence

Record:
- OS and Rust version on both machines;
- each node's PeerId (never share private key files);
- listener address and firewall scope;
- successful connection evidence from both nodes;
- exact test envelope and receiver log showing the same envelope;
- timestamps and any disconnect/retry errors.

Pass only if node B connects to node A and node A receives the exact valid test envelope published by node B (or vice versa). Repeat in the opposite direction if bidirectional behavior is required.

## Troubleshooting

- **No connection:** verify LAN reachability, IP, port, firewall, listener bind address, and exact PeerId.
- **No message:** verify both nodes joined the same GossipSub topic and that the receiver subscribed before publication; retry after both subscriptions are visible.
- **Works on LAN but not across networks:** expected until public bootstrap/relay and NAT traversal are designed and tested. Do not solve this by opening broad inbound firewall access.
- **Identity changes:** preserve each node's local `.osiris-node-key` securely; do not copy one machine's identity key to another. Back up keys only through an approved secure process.

## Current known limitations

- Kademlia uses an in-memory store; peer records are not durable.
- mDNS is LAN discovery only.
- Internet bootstrap infrastructure, relay/hole-punching, durable replay protection, peer reputation/Sybil resistance, and operational revocation are not established by this test.
- The Python E2E envelope helper is not the same thing as an integrated E2E-encrypted GossipSub data path.
- The P2P node is a standalone Rust process, not a Vercel deployment.
- The FastAPI market-data gateway is not yet bridged to this node.
