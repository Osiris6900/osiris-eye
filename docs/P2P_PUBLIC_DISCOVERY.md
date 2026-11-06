# Public peer discovery and self-sustaining operation

Design and implementation notes for issue #85.

## Goal

Nodes stay alive 24/7 and discover peers across the public internet without
operator intervention: the network keeps itself live and finds other nodes on
its own.

## Components

### 1. OSIRIS-scoped Kademlia DHT

The node runs Kademlia under the dedicated protocol name
`/osiris-eye/kad/1.0.0` instead of the public IPFS DHT, so OSIRIS peers form
their own distributed hash table and never mix with unrelated networks.
Server mode stays enabled so reachable nodes help others discover.

### 2. Identify feeds the DHT

Every `identify::Event::Received` now pushes the peer's listen addresses
(bounded to 8) into Kademlia, so the routing table grows from real, observed
addresses instead of staying limited to configured bootstrap peers.

### 3. Periodic random-walk discovery

Every `OSIRIS_DHT_WALK_INTERVAL_SECS` (default 120, clamped 30..3600) the node
issues `get_closest_peers` for a random PeerId. Returned peers are re-added to
the DHT and up to 4 not-yet-connected peers are dialed per walk, letting the
GossipSub mesh grow across the internet over time.

### 4. Existing self-healing (unchanged)

Bootstrap re-dial, DHT bootstrap refresh, relay reservation renewal and
revocation anti-entropy keep running on their own intervals.

### 5. OS-level liveness

`deploy/p2p/osiris-p2p.service` now uses `Restart=always`, so systemd restarts
the node after crashes and after clean exits alike.

## Trust model (unchanged, fail-closed)

Discovery is not authorization. `OSIRIS_TRUSTED_PEERS` and signed revocations
are still enforced on every discovered peer before dialing, and again on every
connection, subscription and message. A discovered-but-untrusted peer is
logged and ignored.

## Limits (honest boundaries)

- Cold start still needs at least one bootstrap address; the DHT cannot invent
  first contact. Keep at least one always-on bootstrap node running.
- No AutoNAT or DCUtR hole punching yet: nodes behind NAT still need an
  explicit relay reservation.
- DHT metadata (which peer stores which key) is visible to participants; E2EE
  protects observation payloads only.
- This is source implementation. Public-network acceptance evidence is tracked
  separately in docs/P2P_RELEASE_GATES.md.
