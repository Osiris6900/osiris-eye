# OSIRIS P2P Application-Level E2EE Protocol (Design Gate)

**Status:** design specification; not implemented in the live Rust/libp2p pipeline.  
**Scope:** protect observation content from relays and non-recipient peers. This protocol does not authorize trading, establish data truth, or protect broker credentials.

## 1. Current boundary

The live node uses libp2p Noise for authenticated, encrypted transport connections. Observation envelopes are currently serialized as JSON and published to GossipSub. Noise protects each transport connection; it is not recipient-specific application-layer encryption for a broadcast message. The standalone Python `p2p_e2ee.py` helper is not wired into the Rust node.

Until the live integration and acceptance tests land, do not describe the network as application-level E2EE.

## 2. Security goals and non-goals

Goals:

- Only the intended recipient node can decrypt a recipient-addressed observation.
- Bind each encryption public key to the sender's existing libp2p Ed25519 PeerId.
- Authenticate the complete envelope, including routing metadata and ciphertext.
- Reject wrong-recipient, malformed, expired, tampered, and replayed envelopes before exposing plaintext.
- Keep private keys local and fail closed when key binding or trust checks fail.

Non-goals for v1:

- Group encryption or one ciphertext decryptable by every network member.
- Forward secrecy against later compromise of a recipient's long-term decryption key.
- Concealing traffic metadata, sender identity, message size, timing, or topic membership.
- Trusting the truth, provenance, or licensing of an observation merely because it is encrypted and signed.
- Carrying orders, broker credentials, account data, or execution commands.

## 3. Cryptographic construction

Use maintained, audited implementations of:

- X25519 for Diffie-Hellman key agreement.
- HKDF-SHA-256 with protocol-specific domain separation.
- ChaCha20-Poly1305 for authenticated encryption.
- The node's existing libp2p Ed25519 identity for signatures.

Do not implement primitives manually and do not reuse a nonce with the same AEAD key. Use a fresh ephemeral X25519 sender key and cryptographically random salt and nonce for each recipient envelope. Derive a per-message AEAD key from the ephemeral shared secret using HKDF; bind protocol version, sender PeerId, recipient PeerId, key epoch, message ID, and algorithm identifiers as authenticated associated data.

The ephemeral sender key does not by itself provide forward secrecy against compromise of the recipient's long-term private key. Do not claim forward secrecy for this v1 construction. A later session/prekey design must be independently reviewed.

## 4. Authenticated encryption-key binding

A node must publish a signed key-binding record containing:

- protocol/schema version;
- canonical libp2p PeerId;
- X25519 public key;
- monotonically increasing key epoch;
- creation and expiry timestamps;
- supported algorithm suite;
- unique key identifier.

The record is signed by the existing Ed25519 identity key. A verifier must:

1. Parse and validate strict size/format bounds.
2. Recompute PeerId from the supplied Ed25519 public key and require an exact match.
3. Verify the signature over canonical, domain-separated bytes.
4. Check expiry and allowed algorithm suite.
5. Enforce monotonic key epoch and reject rollback.
6. Check the PeerId against local enrollment and revocation policy.
7. Persist accepted key epochs/records before treating the binding as trusted.

Never trust an X25519 public key merely because it arrived in a GossipSub message. A key announcement is data, not a trust grant. Unknown peers remain untrusted unless local enrollment policy explicitly admits them.

## 5. Recipient-addressed envelope

The encrypted envelope should contain bounded, versioned fields:

- schema/version and algorithm identifiers;
- sender PeerId and sender key epoch;
- recipient PeerId and recipient key epoch/key ID;
- ephemeral X25519 public key;
- salt and nonce;
- message ID and creation/expiry timestamps;
- ciphertext and AEAD tag;
- Ed25519 signature over canonical header plus ciphertext.

The recipient must verify sender PeerId/key binding and signature, recipient identity and key epoch, field bounds, expiry, and replay state before decryption. AEAD authentication must succeed before plaintext is parsed as an observation. Plaintext observation schema and payload digest validation still apply after decryption.

## 6. GossipSub delivery model

GossipSub is broadcast. For v1, create a separate ciphertext envelope for each intended recipient and publish those envelopes to the encrypted-message topic. Every receiving node may see the outer routing metadata, but only the matching recipient should attempt decryption. Non-recipient nodes must discard without treating the payload as an observation.

Do not encrypt once to a shared static group key. Group membership, removal, key rotation, and compromise recovery require a separate group-key management design. Avoid placing sensitive content or secrets in topic names and routing metadata.

## 7. Replay, persistence, and rotation

- Use a durable, node-local replay store with atomic check-and-record semantics.
- Persist accepted sender key epochs and recipient key epochs; reject rollback.
- Never evict replay IDs silently. Capacity exhaustion must fail closed and alert the operator.
- Key rotation must publish a new signed epoch and retain a bounded, explicit overlap policy.
- Revocation must override cached key bindings immediately when local policy says a peer is revoked.
- A lost or compromised recipient key requires revocation, replacement key binding, and a documented recovery process.

The SQLite replay guard in PR #67 is a standalone Python helper improvement. It does not yet protect the live Rust/libp2p pipeline.

## 8. Required tests before enabling the live path

Unit tests:

- valid key-binding signature and PeerId match;
- wrong signing key, wrong PeerId, tampered record, expired record, unsupported suite;
- key-epoch rollback and revoked peer;
- recipient mismatch, malformed/oversized envelope, tampered header/ciphertext;
- AEAD round-trip and wrong-key failure;
- duplicate message ID across process restart and concurrent workers;
- no plaintext observation emitted before all checks succeed.

Integration tests:

- two nodes exchange a recipient-addressed encrypted observation;
- a third non-recipient node receives the GossipSub envelope but cannot decrypt or emit plaintext;
- wrong recipient and untrusted sender are rejected;
- key rotation and revocation invalidate old bindings;
- restart preserves key epoch and replay state;
- cross-host tests run on independent hosts, not only localhost.

## 9. Rollout gates

1. Land and review this protocol contract.
2. Implement key-binding verification and crypto unit tests in Rust.
3. Add durable key-epoch/replay persistence.
4. Integrate recipient-addressed envelopes into a separate GossipSub topic, keeping plaintext topic compatibility disabled by default only after migration tests pass.
5. Add two-node and three-node integration tests.
6. Conduct independent cryptographic/security review before public beta.

No live E2EE claim or production designation is permitted until these gates are met. Live trading remains disabled and outside this protocol.
