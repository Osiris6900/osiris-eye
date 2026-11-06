# Verified Rust receipt → Python ledger: integration contract (Issue #142)

## Existing boundaries (verified in repository)

- Rust verifier: `services/p2p-node/src/main.rs`, the `pending_receipts.accept(...)` success branch, after authenticated GossipSub source, key binding and signed receipt checks.
- Rust receipt schema and tracker: `services/p2p-node/src/delivery_receipt.rs`.
- Current Rust success log: `observation_delivery_acknowledged recipient=... message_id=... envelope_sha256=...`.
- Python persistence: `services/quant-api/src/osiris_api/recipient_receipt_ledger.py`, `RecipientReceiptLedger.acknowledge_verified`.
- Python outbox: `services/quant-api/src/osiris_api/observation_outbox.py` acknowledges **local stdin only**.

## Trust boundary: fail closed

**Do not** parse the human-readable Rust stdout log and set `verified=True` in Python.
An attacker or unrelated process can forge such a line. The current Python
`verified` parameter is a *caller assertion*, not a cryptographic proof.

Before wiring an adapter, provide an authenticated local transport from the
Rust verifier to the Python ledger process. Prefer a dedicated Unix-domain
socket with restrictive ownership/permissions and peer credential checks,
plus a single operator-managed process identity; on platforms without reliable
peer credentials, require a mutually authenticated channel. Do not expose
an HTTP endpoint accepting a user-controlled `verified` flag.

## Proposed event envelope (not implemented)

```json
{
  "schema": "osiris.p2p.verified-receipt-event.v1",
  "event_id": "unique-opaque-id",
  "sender_peer_id": "local-publisher-peer-id",
  "recipient_peer_id": "verified-signer-peer-id",
  "message_id": "encrypted-envelope-message-id",
  "envelope_sha256": "64-lowercase-hex",
  "accepted_at": 0
}
```

Rust emits this only after successful `pending_receipts.accept`. Python
must verify the authenticated channel identity, schema, event bounds, and
exact (message_id, recipient_peer_id, envelope_sha256) correlation.
Unknown, conflicting, replayed and stale events fail closed. An event is
**not** a durable observation-storage or execution acknowledgement.

## Crash and recovery rules

1. Register pending recipient correlation durably before publishing.
2. Rust verifies a recipient admission receipt; emit on trusted channel.
3. Python atomically records the verified acknowledgement and deduplicates
   event ID before confirming consumption to Rust.
4. If the channel fails or either process crashes before the commit, retain
   pending/unconfirmed state; never fabricate success.
5. A retry of an event must be idempotent. Outbox local-delivered semantics
   remain unchanged; automatic retransmission stays disabled.

## Required implementation and test gates

- Implement and review Rust authenticated event emitter and Python receiver.
- Verify channel authentication independently; test forged stdout and
  untrusted client requests.
- Test wrong sender/recipient/digest, invalid signature, replay, duplicate,
  out-of-order, expiration, restart, lost ack and partial write.
- Run real Rust verifier + Python SQLite integration tests across process
  restarts and at least two hosts before claiming remote delivery guarantees.
- Keep `OSIRIS_DELIVERY_RECEIPTS` opt-in and fail closed on transport outage.

**Status:** contract only. No event transport, receiver or automatic retry
is implemented by this document.
