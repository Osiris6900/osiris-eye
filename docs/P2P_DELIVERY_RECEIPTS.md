# E2EE receiver delivery receipts

The Rust node can request and verify an acknowledgement from the intended
recipient of an encrypted observation. This is an opt-in network-admission
receipt: it records that the recipient decrypted and validated the observation
and persisted its replay-admission ID before signing a receipt.

## Enable

Set both flags on the publishing node and each recipient that should issue
receipts:

```bash
export OSIRIS_E2EE_ENABLED=1
export OSIRIS_DELIVERY_RECEIPTS=1
```

`OSIRIS_DELIVERY_RECEIPTS` defaults to `0` and accepts only `0` or `1`. Enabling
it without E2EE refuses startup. Existing recipient configuration, signed key
bindings, peer authorization and revocation checks still apply; see the
[E2EE configuration guide](P2P_LIVE_E2EE_ROLLOUT.md).

A recipient with receipts disabled can still accept an encrypted observation.
The sender receives no acknowledgement from that recipient. A local stdin
write or successful GossipSub publish never substitutes for a receipt.

## Receipt contract

Receipts use the dedicated signed GossipSub topic
`osiris/e2ee/receipts/1` and schema `osiris.p2p.delivery-receipt.v1`.

| Field | Meaning |
| --- | --- |
| `sender_peer_id` | Original observation publisher, which must be the local node accepting the receipt |
| `recipient_peer_id` | Intended observation recipient and receipt signer |
| `message_id` | Original encrypted envelope's random message ID |
| `envelope_sha256` | SHA-256 of the exact encrypted envelope bytes published and received |
| `accepted_at` | Recipient's Unix time in seconds when it created the receipt |
| `signature` | Recipient identity signature over the domain-separated receipt fields |

The receipt contains no observation payload, instrument, provider or plaintext
observation digest. The receipt itself is signed, not encrypted: PeerIds,
encrypted message IDs, ciphertext digests and timing remain visible to peers
receiving this topic. It does not conceal routing metadata.

The recipient issues a receipt only after successful E2EE decryption,
observation-contract and configured freshness validation, and durable replay-ID
admission. Invalid observations, wrong recipients, replay rejection and replay
storage failures do not produce an acceptance receipt.

The sender tracks a pending receipt only after successful publication. Before
accepting one, it requires an authenticated GossipSub source, current peer
authorization and a verified key-binding identity. The signature must bind the
expected recipient, local original sender, message ID, exact ciphertext digest
and acceptance time. A valid receipt consumes that pending entry once. Invalid,
wrong-peer, unsolicited, duplicate and expired receipts do not confirm delivery
or consume an otherwise valid pending entry.

## Bounds and failure states

- Receipt messages are limited to 4,096 bytes before parsing; unknown fields and
  malformed or oversized fields are rejected.
- Each sender tracks at most 4,096 pending receipts. A full tracker refuses new
  receipt-enabled publication instead of publishing an untracked observation or
  evicting an existing entry.
- Pending entries expire after 120 seconds using a monotonic clock. Receipt
  timestamps may be at most 120 seconds old or 30 seconds ahead of the sender's
  wall clock.
- Pending tracking is in memory. A sender restart does not restore it, and an
  old receipt cannot confirm an observation without its matching pending entry.

The sender reports `observation_delivery_acknowledged` only for a verified
receipt. A timeout reports `observation_delivery_unconfirmed`; it does not prove
that the recipient rejected or failed to receive the observation. Receipt
publication can also fail after recipient admission, so missing acknowledgement
is an unknown outcome.

This first receipt path does not retransmit automatically. A recipient crash
after replay-ID persistence but before receipt publication can leave the sender
unconfirmed; the same encrypted envelope remains replay-rejected after restart.
Do not clear replay history or re-admit a duplicate to manufacture an
acknowledgement.

## Evidence boundary and outbox integration

A verified receipt proves that the named peer signed an admission assertion.
A compromised recipient can lie. The replay journal persists an admission ID,
not the observation payload: a receipt does not establish durable observation
storage, research-store ingestion, application processing, market truth or
exactly-once delivery.

The Python [observation outbox](UNIFIED_OBSERVATION_OUTBOX.md) keeps its existing
local stdin acknowledgement semantics. This receipt feature does not change its
`delivered` flag, clear pending rows, or promote an outbox entry to remote
delivery. Connecting verified receipts to durable per-recipient delivery state
requires a separate adapter, persistence and recovery contract.

## Validation

From the repository root:

```bash
cargo test --locked --manifest-path services/p2p-node/Cargo.toml --all-targets
cargo build --locked --release --manifest-path services/p2p-node/Cargo.toml
python scripts/test_p2p_delivery_receipts.py
```

The local process harness verifies recipient admission, exact receipt
correlation and sender acknowledgement while an excluded third node cannot
decrypt the observation. It also checks fail-closed receipt configuration.
Rust adversarial tests exercise signature, identity, digest, replay, time and
capacity checks. These local tests do not satisfy the independent-host or public
operations [release gates](P2P_RELEASE_GATES.md).
