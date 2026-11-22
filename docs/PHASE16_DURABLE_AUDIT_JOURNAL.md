# Phase 16 — Durable SQLite Audit Journal

Phase 16 extends Phase 15's portable hash-chain evidence with a local SQLite-backed append API.

## Endpoints

- `POST /platform/audit/durable-append` accepts one event with unique `event_id`, timestamp string, actor, action, and optional object-valued details.
- `GET /platform/audit/durable-verify` reads the stored sequence and verifies sequence continuity, previous-hash linkage, event IDs, and SHA-256 digests.

Appending uses a SQLite `BEGIN IMMEDIATE` transaction so concurrent writers serialize while selecting the next sequence and committing the new digest. Duplicate event IDs are rejected. The journal shares the configured `OSIRIS_DB_PATH` database with the research store.

## Signed external anchor

`DurableAuditStore.append` stamps the actor and timestamp on the server and
ignores caller sequence and hash fields. When `OSIRIS_AUDIT_ANCHOR_PATH` points
at a file outside the database, each committed append refreshes a checkpoint
there. `verify()["evidence_status"]` is `ok` only when that checkpoint's
Ed25519 signature and head match. A consistent rewrite from genesis stays
`valid` on the in-database hash walk and is `tampered` on `evidence_status`
once an earlier signed checkpoint exists. With no key file, the checkpoint is
`unsigned` and the evidence status is `unverifiable`. See
`docs/AUDIT_EVIDENCE_KEYS.md`.

## Security boundary

This is durable local storage with an append-only application API, not immutable/WORM storage. A database owner or privileged process can rewrite rows; there is no external signed checkpoint, independent key custody, actor authentication, trusted timestamping, replication, retention policy, or backup guarantee. Production deployment requires access controls, separately protected signing keys/checkpoints, independent replication, tested backups, and monitoring. Hash integrity does not establish that event content is truthful or authorized.

Verification never authorizes new risk or live execution.
