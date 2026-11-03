# Global signed peer revocation — protocol v1 foundation

## Scope of this change

This change adds the cryptographic record format, authority-key verification, durable local registry, per-peer monotonic sequence checks, startup enforcement, and a first GossipSub synchronization path. Nodes with the same pinned authority key subscribe to a dedicated revocation topic, request a registry snapshot when a peer subscribes, validate incoming records, persist accepted records, disconnect newly revoked peers, and re-publish accepted records for mesh propagation. This is an initial synchronization implementation; do not treat it as production-global until multi-process, offline-node, partition-healing, and load tests pass.

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

- GossipSub distribution and anti-entropy synchronization of signed records.
- Durable per-peer sequence checkpoints shared across registry snapshots.
- Authority-key rotation with threshold/quorum authorization and rollback protection.
- Expiry, un-revoke/appeal policy, and audit export.
- Tests spanning multiple independent node processes with online and offline peers.

The current branch includes best-effort GossipSub snapshot/request and record propagation. Nodes must still be provisioned with the same authority key, and revocation changes will not propagate while a node is offline unless it reconnects to a peer holding the updated registry. Periodic anti-entropy, snapshot pagination, and multi-node acceptance tests remain release blockers. This is **not yet production-certified global synchronization**.
