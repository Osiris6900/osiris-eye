# Global signed peer revocation — protocol v1 foundation

## Scope of this change

This change adds the cryptographic record format, authority-key verification, durable local registry, per-peer monotonic sequence checks, startup enforcement, and a first GossipSub synchronization path. Nodes with the same pinned authority key subscribe to a dedicated revocation topic, request registry pages when a peer subscribes, validate incoming records, persist accepted records, disconnect newly revoked peers, and re-publish accepted records for mesh propagation. Nodes also issue periodic anti-entropy requests (default 60 seconds; configurable with OSIRIS_REVOCATION_SYNC_INTERVAL_SECS, clamped to 30–3600 seconds). Registry pages are ordered by PeerId and bounded to 128 records per page. This is an initial synchronization implementation; do not treat it as production-global until offline-node, partition-healing, multi-region, and load tests pass.

## Trust model

Each deployment must provision the same out-of-band Ed25519 authority public key to every node using `OSIRIS_REVOCATION_AUTHORITY_PUBLIC_KEY` (64 lowercase/uppercase hex characters). The corresponding private signing key must remain offline or in a separately secured signing service; it must never be stored on P2P nodes or committed to this repository.

The node optionally loads a JSON array registry from `OSIRIS_REVOCATION_REGISTRY`. The default path is `.osiris-revocations.json`. Every record is verified against the configured authority key before it can affect peer admission. A malformed registry or invalid signature fails node startup closed.

## Signed record

The JSON record fields are:

- `schema`: exactly `osiris.revocation.v1`
- `authority_key`: Ed25519 public key, hex
- `peer_id`: libp2p PeerId to revoke
- `sequence`: positive, strictly increasing per PeerId
- `issued_at`: Unix timestamp in seconds
- `reason_code`: non-empty operator reason code
- `signature`: Ed25519 signature, hex

The signature covers deterministic compact JSON serialization of the first six fields in the struct order. The authority public key is pinned by node configuration; a record cannot nominate its own trusted authority. A record with a sequence less than or equal to the latest stored sequence for that PeerId is treated as a replay and ignored.

## Node configuration

- `OSIRIS_REVOCATION_AUTHORITY_PUBLIC_KEY`: required to enable the signed registry.
- `OSIRIS_REVOCATION_REGISTRY`: optional path to the JSON registry.
- `OSIRIS_REVOKED_PEERS`: remains supported as a local emergency deny-list. Both local and signed revocations are enforced, and revocation overrides the trusted-peer allowlist.

Do not enable a registry with a placeholder or unverified authority key. Provision the public key through trusted configuration management.

## Not yet implemented

- Direct per-peer routing and responder diversity policy; protocol v2 correlates broadcast responses but does not provide private unicast.
- Durable per-peer sequence checkpoints shared across registry snapshots.
- Authority-key rotation with threshold/quorum authorization and rollback protection.
- Expiry, un-revoke/appeal policy, and audit export.
- Automated offline-node and partition-healing recovery tests, plus multi-region/load tests.

The implementation now has periodic anti-entropy requests and bounded paginated registry exchange. A returning node can request current pages from connected peers, while accepted records continue to propagate through the mesh. Nodes must still be provisioned with the same authority key. Protocol v2 binds each request to the authenticated GossipSub publisher PeerId and each response to its request ID, intended requester, and authenticated responder PeerId. Multiple distinct responders may answer the same request. Legacy unbound SnapshotPage responses are rejected. Requests remain broadcast, so this is correlation rather than private routing; request IDs are bounded by a short-lived in-memory pending cache and are not durable across restart. This is **not yet production-certified global synchronization**.


## Request-bound synchronization v2

- A page request carries a 256-bit hexadecimal request ID and the requester PeerId. The receiver requires the declared requester to equal the authenticated GossipSub propagation source.
- A page response echoes the request ID and requester PeerId, and declares its responder PeerId. The requester requires the ID to be pending, the requester field to match its own PeerId, and the responder field to match the authenticated GossipSub source.
- Pending request IDs expire after 120 seconds and the in-memory cache is capped at 256 entries. Multiple peers may respond to the same broadcast request; a response does not consume the pending ID.
- Legacy unbound page responses are rejected. This is not private unicast: GossipSub still broadcasts request and response payloads to topic subscribers.
- Request IDs currently derive from the local PeerId, high-resolution time, and cursor. A future hardening step should use an OS CSPRNG nonce and persist anti-entropy checkpoints.
