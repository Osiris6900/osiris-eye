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

## Cross-module integration contract
- Cryptography: accept only authenticated, provenance-tagged observations from approved peers; verify receipt-to-ledger binding before using remote data. Track #177 and #171.
- AI Research: normalize event time and availability time, then pass point-in-time snapshots to walk-forward research (#178); prohibit future-data leakage.
- Risk Engine: stale, unentitled or invalid feed state must block new paper orders (#179).
- Admin: expose redacted feed health, peer trust and entitlement status through RBAC endpoints (#180).
- Evidence: offline replay, stale-feed rejection, and P2P-to-research-to-risk integration tests (#182).
