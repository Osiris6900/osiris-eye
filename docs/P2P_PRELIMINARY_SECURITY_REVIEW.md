# Preliminary P2P Security Review

Original reviewed baseline: `245c75f3ec6040911d580ea2339f1654582811b6` (2026-11-04).

Source findings rechecked against `4e18c70ee85c3457af7eb251501ee3c06d3ddd80`
on 2026-11-06. The status below describes that revision; later changes require
their own verification.

This is a preliminary source-level engineering review, not an independent cryptographic audit, penetration test, or production certification.

## Finding P2P-AVAIL-001: replay-cache exhaustion

Severity: Medium (availability).

Status: open, with additional fail-closed storage hardening implemented.

[`ReplayGuard`](../services/p2p-node/src/main.rs) loads every persisted replay
identifier into a `HashSet`. Encrypted messages and plaintext observations share
an append-only journal with a 64 MiB on-disk limit. Records contain an acceptance
timestamp and a digest, rather than an envelope expiry. New identifiers are
rejected at capacity; existing duplicate identifiers remain rejected. The
encrypted envelope lifetime is capped at 24 hours, but expired entries are not
pruned. A sustained sequence of otherwise admissible new messages can therefore
exhaust capacity and block new message acceptance. The disk limit also does not
mean the `HashSet` uses at most 64 MiB of memory.

The current implementation checks file size before loading, bounds record
parsing, restricts journal file permissions, rejects symlinks, detects external
truncation/replacement, and persists identifiers before acceptance. These controls
reduce corruption and allocation risks; they do not reclaim expired entries.

Required remediation:
1. Persist each encrypted message's expiry alongside its replay identifier, with an explicit migration policy for existing records.
2. Prune only entries whose replay window has definitively ended, including clock-skew allowance.
3. Compact crash-safely using an atomic replacement, file fsync, and parent-directory fsync.
4. Keep strict disk and memory bounds; if compaction fails, fail closed without discarding live replay identifiers.
5. Add tests for expiry pruning, duplicate rejection across restart, interrupted compaction, corrupt cache, and capacity pressure.

Existing `ReplayGuard` tests cover restart persistence, corrupt/oversized records,
external journal changes, private-file creation and fail-closed capacity pressure.
Expiry pruning and compaction are still missing. A retention design must also
distinguish encrypted envelopes from plaintext observations: historical research
mode can admit old observations, so their replay identifiers cannot simply be
evicted using the encrypted envelope's 24-hour lifetime.

## Finding P2P-OPS-002: health endpoint exposure

Severity: Low / configuration risk.

Status: partially mitigated by the optional hardened deployment profile; open
for directly configured node processes.

The health listener can be bound through `OSIRIS_HEALTH_HTTP_ADDR`. The default
is `127.0.0.1:9090`, but a non-loopback bind exposes node identity, uptime, and
peer/bootstrap connectivity metadata through `/health` and `/ready` without
authentication. The Rust listener does not reject or warn about non-loopback
binds. The optional [deployment preflight](../deploy/p2p/preflight.py) rejects
them, and the [hardened deployment guide](P2P_HARDENED_DEPLOYMENT.md) documents
loopback binding. Processes launched outside that profile do not inherit its
guard.

Remaining remediation: warn about or reject non-loopback binds in direct node
launches, retain the documented loopback-only deployment profile, and require an
authenticated network boundary for intentional remote health access.

## Controls observed

- Ed25519 identity signatures bind transport public keys to PeerIds and key epochs.
- X25519, HKDF-SHA256, and ChaCha20-Poly1305 provide recipient-specific encryption.
- Envelope metadata and ciphertext are signed; signature verification precedes decryption.
- All-zero X25519 shared secrets are rejected.
- Durable peer-key storage and signed revocation synchronization exist.
- Encrypted-message replay identifiers are persisted before plaintext acceptance.
- The three-host harness contains intended-recipient delivery and non-recipient discard checks; its presence does not establish a successful independent-host run.

These are source observations, not proof of production security.

## Remaining review work

- Implement expiry-aware replay retention/compaction for P2P-AVAIL-001 and add its missing regression tests without weakening existing fail-closed behavior.
- Close the direct-process configuration gap in P2P-OPS-002; the optional deployment profile alone does not enforce every launch path.
- Review key rotation, stale bindings, revocation races, replay, and clock skew.
- Fuzz key-binding and encrypted-envelope parsing.
- Test malformed/oversized GossipSub messages and peer resource exhaustion.
- Review SSH/Tailscale workflow trust boundaries and artifacts.
- Obtain independent cryptographic review before production claims.

Do not enable live broker routing based on this preliminary review.
