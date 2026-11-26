# Phase 17 — External Audit Checkpoints

Phase 17 adds operator-held SHA-256 checkpoints over the durable Phase 16
journal. A checkpoint records an anchor ID, UTC timestamp, sequence, event
count, and journal head hash. Verification checks the referenced sequence and
hash against the current journal, detects missing checkpoint rows (including
common truncation/rollback cases), and first requires the current journal to
verify cleanly. A checkpoint remains verifiable after later events are appended.

## API

- `POST /platform/audit/checkpoint-create` — body: `{"anchor_id":"vault-2026-11-03"}`
- `POST /platform/audit/checkpoint-verify` — body: `{"checkpoint":{...}}`

The checkpoint returned by this module is still an unsigned JSON record.
`audit_evidence.checkpoint_evidence` and `scripts/verify_audit_evidence.py`
write the signed file outside the database. See `docs/AUDIT_EVIDENCE_KEYS.md`.

## Operational requirements and limitations

Copy checkpoint JSON to an independently controlled system (for example,
a separately administered immutable evidence store). If the checkpoint is
stored only beside the SQLite database, a privileged actor may rewrite both.
This is not a digital signature, trusted timestamp, external-anchor authenticity
check, WORM guarantee, replication service, or proof that the original event
was true. SHA-256 integrity is not actor authentication. Any invalid or
unavailable evidence must remain a control failure; this feature never enables
new risk or live execution.
