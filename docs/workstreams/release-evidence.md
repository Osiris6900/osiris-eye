# Release evidence and deployment — parallel workstream

Status: started (branch and acceptance checklist created); implementation and verification are pending.

## Implementation and acceptance
- [ ] Require immutable action references and dependency provenance
- [ ] Generate and validate Rust/npm SBOM; resolve Python deployment lock
- [ ] Verify staging smoke tests, backups, restore and failover
- [ ] Require linked cryptography review and paper-only end-to-end evidence
- [ ] Block production release when any gate is missing

## Release constraints
No exchange credentials, live orders, public-node rollout or production claims from this branch. Merge only after actual code, reproducible tests, CI and review.

## Cross-module integration contract
- Cryptography: retain independent review, revocation/replay tests, OSV remediation and exact binary hashes (#171, #177).
- AI Research: reproducible data provenance, walk-forward reports, leakage/cost/drift checks and approved model versions (#178).
- Risk Engine: demonstrate independent kill-switch, stale-feed block, journal reconciliation and recovery (#179).
- Admin: demonstrate authenticated RBAC, audit/redaction, monitoring, restore and operator incident drills (#180).
- Evidence: stage-by-stage CI gates, SBOM, immutable action pinning, staging smoke tests and cross-module release matrix (#181, #182, #207). Live execution requires separate approval.
