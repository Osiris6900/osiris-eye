# Preliminary P2P Security Review

Reviewed baseline: 245c75f3ec6040911d580ea2339f1654582811b6
Date: 2026-11-04

This is a preliminary source-level engineering review, not an independent cryptographic audit, penetration test, or production certification.

## Finding P2P-AVAIL-001: replay-cache exhaustion

Severity: Medium (availability).

The replay cache loads every persisted identifier into memory. Encrypted-message identifiers are appended permanently, and new encrypted messages are rejected when the cache reaches 64 MiB. The encrypted envelope lifetime is capped at 24 hours, but old replay entries are not pruned after their acceptance window ends. The cache therefore grows monotonically and can eventually cause the node to reject all new encrypted messages.

Required remediation:
1. Persist each encrypted message's expiry alongside its replay identifier.
2. Prune only entries whose replay window has definitively ended, including clock-skew allowance.
3. Compact crash-safely using an atomic replacement, file fsync, and parent-directory fsync.
4. Keep strict disk and memory bounds; if compaction fails, fail closed without discarding live replay identifiers.
5. Add tests for expiry pruning, duplicate rejection across restart, interrupted compaction, corrupt cache, and capacity pressure.

## Finding P2P-OPS-002: health endpoint exposure

Severity: Low / configuration risk.

The health listener can be bound through OSIRIS_HEALTH_HTTP_ADDR. The default is loopback, but a non-loopback bind exposes node identity, uptime, and peer/bootstrap connectivity metadata without authentication.

Required remediation: warn on non-loopback binds, document loopback-only default, and require an authenticated network boundary for remote health access.

## Controls observed

- Ed25519 identity signatures bind transport public keys to PeerIds and key epochs.
- X25519, HKDF-SHA256, and ChaCha20-Poly1305 provide recipient-specific encryption.
- Envelope metadata and ciphertext are signed; signature verification precedes decryption.
- All-zero X25519 shared secrets are rejected.
- Durable peer-key storage and signed revocation synchronization exist.
- Encrypted-message replay identifiers are persisted before plaintext acceptance.
- The three-host harness checks intended-recipient delivery and non-recipient discard before decryption.

These are source observations, not proof of production security.

## Remaining review work

- Remediate P2P-AVAIL-001 and add fault-injection regression tests.
- Review key rotation, stale bindings, revocation races, replay, and clock skew.
- Fuzz key-binding and encrypted-envelope parsing.
- Test malformed/oversized GossipSub messages and peer resource exhaustion.
- Review SSH/Tailscale workflow trust boundaries and artifacts.
- Obtain independent cryptographic review before production claims.

Do not enable live broker routing based on this preliminary review.
