# Three-host P2P E2EE non-recipient acceptance

Status: harness and manual workflow implemented; passing cross-host evidence is
still required for the exact release commit.

## Objective

Extend the two-host recipient-delivery proof with a third, independently running
P2P node that is connected to the same network but is not an intended recipient.
The test must demonstrate exact recipient delivery and explicit discard by the
non-recipient. Separate primitive tests cover wrong-key decryption failure; a
recipient-discard path is not an attempted cryptographic break.

## Topology

- Host A: sender and controller.
- Host B: sole intended recipient.
- Host C: connected observer, explicitly excluded from the recipient set.

All three hosts must be separate OS processes on separate machines for the
cross-host acceptance. Use fresh throwaway identity, transport, peer-key,
replay-cache, and E2EE state for every run. Pin the tested commit SHA.

## Required protocol assertions

1. A, B, and C establish authenticated P2P connections.
2. A verifies and durably stores signed key bindings for B and C; B and C
   verify A's binding.
3. A constructs one observation addressed only to B's PeerId.
4. B decrypts and reports the exact instrument identifier and payload SHA-256.
5. C receives the encrypted-topic envelope and drops it because its PeerId is
   not the addressed recipient; C must not emit the plaintext instrument or
   payload digest.
6. This cross-host test proves recipient-gated discard, not a deliberate
   decryption attempt on C. Wrong-recipient and wrong-private-key cryptographic
   failures remain covered by the E2EE primitive tests; do not claim C attempted
   decryption when the live node correctly rejects the envelope before decrypting.
7. A records the exact recipient set and a run correlation ID. Logs must not
   contain private keys, secret material, or plaintext payload.
8. The test fails closed on disconnect, stale key binding, invalid signature,
   replay, wrong recipient, malformed envelope, or timeout.

## Test evidence

A passing run must retain:
- commit SHA, workflow run URL, and timestamps;
- PeerIds for A, B, and C (public identifiers only);
- recipient-set assertion showing B included and C excluded;
- B's exact decrypted payload digest;
- C's explicit non-recipient discard result;
- sanitized logs and test exit status.

## Security boundaries

This acceptance does not prove resistance to compromised endpoints,
administrator access, traffic analysis, denial of service, or long-term key
compromise. It is not an independent cryptographic audit or production
security certification. No live broker credentials or order routing are
authorized by this test.

## Implemented test paths

- `scripts/test_p2p_three_host_e2ee_ssh.py` launches local A and SSH-managed B/C
  with fresh node state and bounded cleanup. It selects B as the sole recipient,
  checks B's exact observation digest and C's explicit non-recipient discard, and
  rejects plaintext identifiers/digests in C's logs.
- `.github/workflows/p2p-three-host-e2ee.yml` is manually dispatched and uses
  three separate runners on a private test overlay. Same-run artifacts carry
  public SSH keys/endpoints; host public keys are pinned before SSH. Private
  keys and private node state must never be uploaded.
- Two-host acceptance remains available through
  `scripts/test_p2p_cross_host_e2ee_ssh.py` and its separate workflow.

## Current evidence and completion gate

The baseline local three-process E2EE test passed recipient delivery and
non-recipient rejection. The three-host harness and workflow exist, but no
passing three-host run has been established. The hardening branch repairs
workflow syntax, host coordination and SSH host-key pinning; those changes still
require execution on separate hosts with the configured test overlay.

Before marking this gate complete, retain the evidence listed above and verify
that all three identities are distinct, every node remains connected during the
probe, B decrypts the exact digest and C explicitly discards the envelope. A green
build, a two-host result or three local processes cannot substitute for this
acceptance. Public Internet/NAT reachability, hostile-load resilience and
independent cryptographic audit remain separate gates.
