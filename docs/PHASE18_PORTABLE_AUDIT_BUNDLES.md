# Phase 18 — Portable Audit Evidence Bundles

Phase 18 adds a portable JSON export of the durable Phase 16 audit journal,
optionally carrying a Phase 17 checkpoint. Verification is offline: it checks
the bundle digest, every event's sequence and hash-chain link, unique event IDs,
the head hash, and checkpoint-to-event correspondence.

## API

- `POST /platform/audit/bundle-export` — body: `{"bundle_id":"evidence-001","checkpoint":{...}}`
- `POST /platform/audit/bundle-verify` — body: `{"bundle":{...}}`

The export endpoint returns the complete evidence bundle; preserve the exact
JSON bytes in independently controlled storage. The SHA-256 `bundle_hash` is
the integrity digest. When `OSIRIS_AUDIT_SIGNING_KEY_FILE` is set, the export
adds an Ed25519 `signature_envelope` over that hash. When it is unset, the
bundle is `signature_status: unsigned` and `signed: false`. `status` remains
the hash result. `evidence_status` is `ok` only for a verified signature over
server-stamped events, `tampered` when the bundle or signature does not match,
and `unverifiable` when the signature or the actor identity is missing. The
hash result is not a trusted timestamp, WORM retention, or proof that a source
event was true. Invalid evidence never permits new risk or live execution. See
`docs/AUDIT_EVIDENCE_KEYS.md`.
