# Phase 18 — Portable Audit Evidence Bundles

Phase 18 adds a portable JSON export of the durable Phase 16 audit journal,
optionally carrying a Phase 17 checkpoint. Verification is offline: it checks
the bundle digest, every event's sequence and hash-chain link, unique event IDs,
the head hash, and checkpoint-to-event correspondence.

## API

- `POST /platform/audit/bundle-export` — body: `{"bundle_id":"evidence-001","checkpoint":{...}}`
- `POST /platform/audit/bundle-verify` — body: `{"bundle":{...}}`

The export endpoint returns the complete evidence bundle; preserve the exact
JSON bytes in independently controlled storage. Verification detects accidental
or unsophisticated tampering, but a plain SHA-256 bundle is not a digital
signature. It does not establish who created the bundle, trusted time, external
custody, truth of source events, WORM retention, or protection against an
attacker who can replace both bundle and digest. Invalid evidence never permits
new risk or live execution.
