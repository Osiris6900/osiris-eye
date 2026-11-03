# OSIRIS EYE Internet P2P Connectivity Design

## Status

Design only. This document does not claim that Internet connectivity, NAT traversal, relay service, or production availability is implemented.

## Current verified baseline

- The Rust node uses TCP + Noise + Yamux, signed GossipSub, Kademlia, mDNS, and Identify.
- CI has passed a two-process test in which two independent node processes connect over loopback and exchange a valid observation envelope.
- mDNS is LAN discovery only. Kademlia currently uses an in-memory store.
- The two-machine runbook covers a reachable LAN listener, not Internet traversal.
- The observation envelope currently carries metadata and a digest string; the node does not transmit the underlying market-data bytes or verify that the digest matches those bytes.

## Target topology

Use a small, explicitly operated public bootstrap/relay node plus ordinary peers:

1. **Bootstrap node**: stable public TCP endpoint and stable PeerId. It helps peers discover one another through Kademlia. Bootstrap is discovery, not a guarantee that two NATed peers can connect directly.
2. **Relay node**: publicly reachable Circuit Relay v2 service. It accepts bounded reservations and forwards peer-to-peer circuits when direct paths are unavailable. It must have strict connection, reservation, bandwidth, duration, and per-peer limits.
3. **Client nodes**: dial configured bootstrap peers, discover peers, attempt direct connections, and use a relay circuit when direct connectivity is unavailable.
4. **Direct-path upgrade**: evaluate AutoNAT and DCUtR (hole punching) after the relay path is proven. Direct upgrade is opportunistic; relay fallback remains necessary for restrictive NATs/firewalls.

A single machine may initially run bootstrap and relay roles, but they should be independently configurable and independently observable. A GitHub Actions runner is ephemeral and is not a durable public bootstrap or relay host. A Vercel serverless function is not a long-lived libp2p TCP listener.

## Proposed configuration contract (not implemented)

- `OSIRIS_BOOTSTRAP_PEERS`: comma-separated, operator-supplied multiaddresses ending in `/p2p/<peer-id>`.
- `OSIRIS_RELAY_CLIENT_ENABLED`: explicit opt-in for relay client behavior; default false until a relay endpoint is configured and tested.
- `OSIRIS_RELAY_PEERS`: comma-separated, operator-approved relay multiaddresses ending in `/p2p/<peer-id>`.
- `OSIRIS_RELAY_SERVER_ENABLED`: explicit opt-in for operating a relay server; default false.
- `OSIRIS_PUBLIC_LISTEN_ADDR`: optional explicit public listener address only when the host actually has a reachable public endpoint.
- Never infer public reachability from a local listen address or provider-readiness setting. Never silently trust arbitrary relay addresses received from the network.

Configuration must fail closed on malformed multiaddresses, missing PeerIds, conflicting role settings, and unapproved relay endpoints. Startup logs must distinguish configured addresses from observed/verified reachability.

## Security and operations requirements

- Keep broker credentials, exchange API keys, account data, orders, and execution commands off the P2P network.
- Pin/trust node identity keys through an explicit enrollment policy; a PeerId alone is not a trust decision.
- Add relay allowlisting, per-peer quotas, rate limits, connection caps, and bounded circuit duration/bytes before enabling relay-server mode.
- Do not expose a relay server to the public Internet without firewall rules, abuse monitoring, resource limits, and an operator responsible for patching and incident response.
- Add durable replay protection and authenticated message/payload integrity before relying on observations operationally.
- Hash verification must be over the exact payload bytes received, not merely a 64-character digest field.
- Keep observation provenance, source timestamp, receive timestamp, and stale-data rejection separate.
- P2P transport availability does not establish market-data correctness, feed entitlements, or trading readiness.

## Implementation sequence

### Phase A — relay client on an approved test relay
- Add the libp2p relay client feature and client behavior/transport integration.
- Require explicit relay configuration; do not add an implicit public relay dependency.
- Reserve a relay address and log reservation success/failure with peer and relay IDs.
- Add unit/config tests and a local relay integration test with three independent processes: relay, peer A, peer B.
- Verify A and B exchange a signed test observation through the relay when direct dialing is unavailable.

### Phase B — controlled relay server
- Add an explicit relay-server role, disabled by default.
- Apply strict reservation, circuit, duration, byte, and per-peer limits.
- Add operational metrics and graceful shutdown.
- Test denial and quota behavior, not only successful forwarding.

### Phase C — NAT traversal
- Add AutoNAT and DCUtR only after relay fallback is reliable.
- Test symmetric/restrictive NAT and firewall cases where possible.
- Record whether each connection is direct or relayed; do not report a direct connection when the relay is carrying it.

### Phase D — real cross-network acceptance
- Run nodes on two separately administered networks, not just two processes on one CI host.
- Capture PeerIds, observed public addresses, connection type, relay reservation, message ID, payload digest verification, and timestamps.
- Repeat with direct path blocked to prove relay fallback.
- A test passes only when the receiver verifies the exact bytes and trusted sender identity.

## Acceptance criteria

Internet P2P must not be called complete until:
- two nodes on separate networks connect using the documented bootstrap/relay configuration;
- relay fallback succeeds when direct connectivity is blocked;
- the receiver verifies sender identity and the digest against actual payload bytes;
- malformed, stale, replayed, oversized, and untrusted messages are rejected;
- relay limits and failure/reconnect behavior are tested;
- logs and metrics clearly identify direct versus relayed paths.

## Infrastructure decision

No paid VPS, hosted relay, domain, or external service should be provisioned without explicit operator approval. Until an approved public endpoint exists, development can validate relay behavior using a local three-process test, but cannot honestly claim public Internet reachability.
