# Live P2P E2EE Integration (Opt-in)

## Status

The Rust/libp2p node now contains an opt-in recipient-addressed application-encryption path. It is **not enabled by default** and is not a production security certification. Existing Noise transport encryption remains active independently.

The opt-in path uses X25519 ephemeral-static agreement, HKDF-SHA-256, ChaCha20-Poly1305, and the node's Ed25519 identity signature. Each recipient gets a distinct ciphertext envelope on `osiris/e2ee/messages/1`. Public signed X25519 key bindings are announced on `osiris/e2ee/key-bindings/1`.

When `OSIRIS_E2EE_ENABLED=1`:
- outbound observations are encrypted separately for each PeerId in `OSIRIS_E2EE_RECIPIENTS`;
- the recipient must have announced a valid key binding and be allowed by local peer policy;
- inbound encrypted envelopes must target this node, match the sender's signed key binding and key epoch, pass signature and AEAD verification, and pass the durable local replay journal before the observation is accepted;
- plaintext observations on the legacy observation topic are rejected.

When the flag is absent or not `1`, the legacy plaintext observation topic remains enabled for compatibility. Do not assume the network is E2EE unless every participating node has explicitly enabled the feature and has compatible key configuration.

## Configuration

Set these on each node before enabling E2EE:

- `OSIRIS_E2EE_ENABLED=1`
- `OSIRIS_E2EE_RECIPIENTS=<comma-separated recipient PeerIds>` or `OSIRIS_E2EE_RECIPIENTS_FILE=<path>` on publishing nodes (the file is read for each observation, so recipient lists can be updated without restarting)
- `OSIRIS_TRANSPORT_KEY=<private key file path>` (optional; defaults to `.osiris-transport-key`)
- `OSIRIS_TRANSPORT_KEY_EPOCH=<positive integer>` (defaults to `1`; increment when rotating the X25519 transport key)
- Existing `OSIRIS_NODE_KEY`, `OSIRIS_TRUSTED_PEERS`, and `OSIRIS_REVOKED_PEERS` controls remain applicable.

The transport private key file is created with owner-only permissions on Unix. Back it up securely. Never transmit it or include it in logs, issue reports, or source control. A changed transport key must use a higher epoch.

## Important limitations / rollout gates

- Key bindings are currently held in process memory; durable peer-key/epoch storage and rollback protection across restarts are still required. Local signed key bindings are republished on peer subscription and refreshed every 24 hours.
- Cross-host two-node and three-node tests (including a non-recipient observer) are still required before broad rollout.
- No independent cryptographic audit has been completed.
- This v1 construction does not claim forward secrecy against later compromise of the recipient's long-term X25519 private key.
- The envelope protects message contents, not sender identity metadata, recipient metadata, timing, message size, or topic membership.
- A key binding is not a trust grant. Local trusted-peer and revocation policy must still admit the peer.
- Never send broker credentials, account data, orders, execution commands, or other secrets through this network.
- Live trading remains disabled and outside this P2P protocol.

Do not describe the network as production-ready or fully secure until durable key-binding persistence, independent cross-host acceptance tests, key rotation/revocation recovery, and independent security review have passed.
