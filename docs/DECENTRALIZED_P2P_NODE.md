# OSIRIS EYE: First decentralized P2P node

## What this adds

The first network node is an isolated Rust service under `services/p2p-node`. It does not replace the existing FastAPI, React, TypeScript, Rust risk-kernel, or SQLite foundations.

- **Transport:** libp2p over TCP, authenticated with Noise and multiplexed with Yamux.
- **Peer discovery:** mDNS for peers on the same local network; Kademlia DHT for peer routing when bootstrap peers are supplied.
- **Data sharing:** signed GossipSub messages on `osiris/observations/1`.
- **Identity:** an Ed25519 libp2p node key persisted locally (default `.osiris-node-key`, override with `OSIRIS_NODE_KEY`). On Unix, the key file is restricted to owner read/write.
- **Message contract:** JSON observation envelope with schema, instrument, observation time, provider, and payload SHA-256. This first node relays metadata and a content hash, not licensed raw market data or broker credentials.

## Run locally

From the repository root:

```sh
cargo run --manifest-path services/p2p-node/Cargo.toml
```

The node prints its Peer ID and listening multiaddress. Keep the generated `.osiris-node-key` private; deleting it creates a new node identity.

## Connect nodes

Set `OSIRIS_BOOTSTRAP_PEERS` to a comma-separated list of peer multiaddresses ending in `/p2p/<peer-id>`:

```sh
OSIRIS_BOOTSTRAP_PEERS="/ip4/203.0.113.10/tcp/4001/p2p/12D3KooW..." \
  cargo run --manifest-path services/p2p-node/Cargo.toml
```

The example address is documentation-only. A real reachable peer address is required for internet-wide DHT bootstrap. Local mDNS discovery works only on a shared local network.

## Publish an observation

Pipe one JSON envelope to the node's standard input:

```sh
echo '{"schema":"osiris.observation.v1","instrument":"BTC-USD","observed_at":"2026-11-03T00:00:00Z","provider":"coinbase-public","payload_sha256":"<64-hex-character-content-hash>"}' | cargo run --manifest-path services/p2p-node/Cargo.toml
```

Use a real SHA-256 digest in production. The current prototype validates the envelope schema but does not independently fetch or verify the referenced payload. Peer signatures authenticate the publishing peer; they do not prove that a market observation is truthful.

## Current limitations

- This is a first P2P node prototype, not a complete decentralized exchange-data network.
- No public bootstrap fleet, relay fleet, NAT traversal guarantee, node reputation, Sybil resistance, economic incentives, or global availability SLA is provided.
- mDNS discovery is LAN-scoped; internet peers require reachable bootstrap addresses.
- The node does not read API secrets, submit broker orders, or enable live trading.
- Before using this for trading decisions, add canonical instrument/time contracts, payload retrieval and hash verification, timestamp/freshness checks, provider entitlement checks, peer trust policy, rate limits, and adversarial network tests.
- A local node can run without Vercel, but internet reachability still requires an internet connection and a reachable public peer or relay.
