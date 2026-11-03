# Three-host P2P E2EE non-recipient acceptance

Status: planned acceptance gate; not yet implemented or passed.

## Objective

Extend the two-host recipient-delivery proof with a third, independently running
P2P node that is connected to the same network but is not an intended recipient.
The test must demonstrate that the third node cannot recover the recipient's
observation plaintext.

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
5. C receives only ciphertext/opaque routing metadata, never the plaintext
   instrument or payload digest.
6. C cannot decrypt the envelope using its own key material; the negative
   assertion must be based on the actual receive/decrypt path, not merely on
   the absence of a success log.
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
- C's explicit decrypt-rejection result;
- sanitized logs and test exit status.

## Security boundaries

This acceptance does not prove resistance to compromised endpoints,
administrator access, traffic analysis, denial of service, or long-term key
compromise. It is not an independent cryptographic audit or production
security certification. No live broker credentials or order routing are
authorized by this test.

## Implementation sequence

1. Add a third-host launch and lifecycle to the existing SSH acceptance
   harness, with separate ephemeral credentials and strict cleanup.
2. Add an explicit non-recipient receive/decrypt rejection signal in the node
   only if the current message path does not expose a testable result.
3. Add a manually dispatched three-host GitHub Actions workflow using three
   independent hosted runners on the private test overlay.
4. Verify the workflow's logs prove both recipient success and non-recipient
   cryptographic rejection; a green build alone is insufficient.
5. Run at least one separate-host acceptance and retain its evidence before
   marking this gate complete.

## Current state

The existing two-host harness proves only recipient delivery between A and B.
The existing local three-process test is not a substitute for a third
independent host. This document records the next acceptance gate; it does not
claim that implementation or execution is complete.
