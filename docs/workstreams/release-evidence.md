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
