# Market data integration — parallel workstream

Status: started (branch and acceptance checklist created); implementation and verification are pending.

## Implementation and acceptance
- [ ] Implement vendor-neutral streaming adapter with explicit entitlement and provenance
- [ ] Reject stale, out-of-order and unlicensed observations
- [ ] Persist availability timestamps and instrument identifiers
- [ ] Add deterministic replay tests and outage recovery
- [ ] Run CI and record evidence; never use synthetic ticks as live prices

## Release constraints
No exchange credentials, live orders, public-node rollout or production claims from this branch. Merge only after actual code, reproducible tests, CI and review.
