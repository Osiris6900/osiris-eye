# Phase 15 — Tamper-Evident Audit Evidence

Phase 15 adds deterministic SHA-256 hash-chain construction and verification for audit-event evidence.

## API

- `POST /platform/audit/chain-build` accepts an ordered `events` array. Each event requires a unique `event_id`, timestamp string, actor, action, and optional object-valued details.
- `POST /platform/audit/chain-verify` checks sequence numbers, previous-hash links, event digests, event count, and chain head.

Each digest covers a canonical JSON envelope containing the sequence, previous digest, and event. Reordering, editing, deleting, or inserting an event invalidates the chain.

## Important limitations

This is a tamper-evident evidence format, not a durable append-only audit database. The API verifies caller-supplied evidence and does not authenticate actors, prove timestamps, provide digital signatures, protect signing keys, or prevent an attacker who can rewrite the entire chain and its head from rebuilding it. Production audit requires separately controlled durable storage, authenticated identity, key management/signatures, retention controls, and independent monitoring.

The verifier never authorizes new risk or live execution. Hash integrity is not proof that an event was truthful or authorized.
