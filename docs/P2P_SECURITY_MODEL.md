# OSIRIS EYE P2P Security Model

## Scope

This document defines the security boundary for the OSIRIS EYE observation network. The P2P network is a transport and coordination layer for public, non-sensitive observations. It is not an order-routing network and it is not a substitute for exchange authentication or market-data licensing.

## Security invariants

1. No broker/exchange API secrets, credentials, account identifiers, balances, positions, orders, withdrawals, or execution commands are permitted in P2P messages.
2. Real-money execution remains disabled. Peer messages cannot enable it or modify local risk limits.
3. Every node has a locally generated Ed25519 identity. The private key is stored locally and must never be transmitted.
4. A PeerId is an identifier, not a trust grant. Trust requires explicit local enrollment or an operator-managed allowlist.
5. Observation payloads are bounded to 1 MiB and SHA-256 is recomputed over the received UTF-8 payload before acceptance.
6. Signed GossipSub messages authenticate the publishing peer at the libp2p protocol layer. This does not prove the truth or licensing of the observation.
7. Relay nodes forward encrypted libp2p connections; they must not be granted application-level authority over observations.
8. All configuration received from remote peers is untrusted input. Remote advertisements cannot grant local permissions.

## Current controls

- Ed25519 node identity and signed GossipSub publication.
- TCP transport secured with Noise and multiplexed with Yamux.
- Schema and metadata bounds on observation envelopes.
- UTF-8 payload size limit and SHA-256 payload verification.
- Local identity key file permissions are restricted to 0600 on Unix.
- Relay and live execution are not enabled by default.

## Threats and required controls

| Threat | Required control | Current status |
|---|---|---|
| Payload tampering | Recompute digest over received bytes | Implemented for UTF-8 payload |
| Publisher impersonation | Signed GossipSub + pinned/enrolled PeerId | Signed messages implemented; enrollment policy pending |
| Replay | Durable message IDs/nonces and shared replay store | Pending |
| Stale market data | Validate source timestamp and local receive time | Pending |
| False but correctly signed data | Provider provenance, independent corroboration, reputation/quarantine | Pending |
| Malicious relay | End-to-end Noise encryption, peer identity checks, bounded relay quotas | Noise transport exists; relay implementation pending |
| Resource exhaustion | Payload caps, rate limits, connection and circuit caps | Payload cap exists; relay limits pending |
| Compromised node key | Key rotation, revocation and re-enrollment procedure | Pending |
| Unauthorized execution | Keep execution plane separate; local policy and explicit authorization | Live execution disabled; separation must remain invariant |

## Observation acceptance pipeline

1. Decode the bounded message.
2. Validate schema and required metadata.
3. Enforce payload size limits before expensive processing.
4. Recompute SHA-256 over exact payload bytes and compare to the declared digest.
5. Authenticate the publisher identity and check local enrollment/trust policy.
6. Validate provider provenance and timestamp freshness.
7. Reject duplicate message IDs using a durable replay store.
8. Store the accepted observation with publisher PeerId, provider, source timestamp, receive timestamp, digest, and validation result.
9. Make the observation available to downstream analytics only; never convert a peer message into an order.

Steps 5–8 are required before observations can be treated as trusted operational inputs. The existing node currently implements only the envelope/payload validation and libp2p message authentication portions.

## Relay policy

- Relay server role must be opt-in and disabled by default.
- Use libp2p Circuit Relay v2, not an application-level proxy.
- Enforce finite reservation count, per-peer reservation count, circuit count, per-peer circuit count, circuit byte limit, circuit duration, and per-peer/IP rate limits.
- Advertise relay service only when the operator has verified a reachable public address.
- Do not treat a relay reservation as proof that two peers can reach each other over the public Internet.
- Record whether each connection is direct or relayed.
- Provide graceful shutdown and monitoring for active reservations, circuits, rejected requests, bytes forwarded, and resource pressure.
- Do not deploy a public relay on ephemeral CI infrastructure or serverless request handlers.

## Cryptography lifecycle

- Keep long-term identity keys separate from ephemeral transport/session keys.
- Do not log private key material, session secrets, API credentials, or raw sensitive environment values.
- Provide a documented identity backup, rotation, and revocation process before broad public enrollment.
- The existing encrypted-envelope helper is a separate cryptographic primitive; it is not yet wired into the live P2P message path. Do not claim application-level end-to-end encryption from that helper alone. libp2p Noise secures transport connections.

## Release gates

A public Internet beta must not be announced until all of the following are verified:

- Relay server/client roles work in a three-process integration test.
- Two peers exchange messages through a relay when direct peer-to-peer dialing is blocked.
- Separate networks can connect using an operator-controlled stable relay/bootstrap endpoint.
- Relay quotas and denial paths are tested.
- Trusted-peer enrollment, durable replay protection, stale-data rejection, key rotation, and audit records are implemented.
- Security review confirms that the P2P network cannot carry or trigger live orders.
