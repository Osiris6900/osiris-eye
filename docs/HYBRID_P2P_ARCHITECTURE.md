# OSIRIS EYE Hybrid P2P Architecture

## Decision
OSIRIS EYE uses a hybrid P2P design: public/untrusted peers may participate in discovery and independently verifiable public-information tasks; owner-approved trusted peers may receive narrowly scoped compute tasks; the owner-controlled core retains policy, model approval, risk limits, credentials, and operational authority.

## First implemented milestone: cryptographic node identity
- Ed25519 node identities and SHA-256-derived node IDs.
- Short-lived, one-time enrollment challenges.
- Proof-of-possession signatures bound to protocol version, challenge ID, nonce, and node ID.
- SQLite local registry; unknown peers default to public_untrusted.
- Explicit public-key allowlisting through OSIRIS_P2P_TRUSTED_PUBLIC_KEYS.
- Operator endpoints: GET /p2p/status, GET /p2p/nodes, POST /p2p/challenge, POST /p2p/enroll.

Enrollment remains behind the existing API bearer-token middleware. This is not yet an open internet join protocol.

## Not implemented yet
No libp2p transport, NAT traversal, relay, DHT/Kademlia, GossipSub, shared multi-region registry, consensus, Sybil/DoS defenses, sandboxed compute, global market-data discovery, or broker execution is included. Local SQLite may be ephemeral in serverless deployments; a shared durable database is required before relying on peer enrollment across instances.

## Security invariants
1. Private node keys remain with their operators; only public keys are submitted.
2. Enrollment requires an Ed25519 proof over a one-time expiring challenge.
3. Unknown peers receive only the public_untrusted classification.
4. Only explicitly allowlisted public keys are classified trusted.
5. Public peers receive no owner-control, credential, market-data-secret, or trading permission.
6. Live execution remains disabled; no peer can bypass the independent risk gate.
7. Never publish private keys, API tokens, broker credentials, or exchange secrets.

## Next milestones
1. Implement real libp2p transport, authenticated handshakes, bootstrap peers, NAT traversal, and rate-limited public enrollment.
2. Add DHT peer discovery and signed, expiring peer advertisements.
3. Move the registry to shared durable storage and add owner-controlled trust/revocation.
4. Add sandboxed compute workers with quotas, signed task manifests, deterministic result checks, and no secrets in worker environments.
5. Enforce source provenance and market-data licensing.
6. Complete adversarial network, abuse, observability, and disaster-recovery testing before public launch.

## Operational status
The /p2p/status endpoint reports transport, discovery, distributed compute, shared registry, and live execution as disabled until those capabilities are implemented and tested.


## End-to-end encrypted message envelope (new milestone)
- X25519 ephemeral sender key agreement with the recipient's separately generated X25519 transport public key.
- HKDF-SHA256 derives a per-message key; ChaCha20-Poly1305 encrypts and authenticates the payload and header.
- The complete envelope is signed by the sender's Ed25519 node identity; receivers must pin/verify the sender public key and node ID.
- Envelopes include recipient/sender IDs, protocol/cipher identifiers, timestamp, random message ID, salt, nonce, and ephemeral public key.
- A bounded process-local replay guard and five-minute clock-skew limit are provided as primitives.
- Payloads are capped at 1 MiB.

### Important limits
This milestone does not open network sockets or implement libp2p, Noise sessions, routing, NAT traversal, or peer discovery. The recipient's static transport private key means this envelope primitive does not provide full forward secrecy against later compromise of that recipient key. Replay protection is process-local and is not safe across multiple server instances or restarts. Production deployment requires durable distributed replay storage, secure hardware-backed key custody/rotation/revocation, rate limits, authenticated transport, and independent security review. Do not use this primitive as a substitute for a mature Noise/libp2p session once the actual network transport is integrated. Live trading remains disabled.
