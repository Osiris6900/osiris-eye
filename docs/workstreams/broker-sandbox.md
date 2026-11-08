# Broker sandbox execution — parallel workstream

Status: started (branch and acceptance checklist created); implementation and verification are pending.

## Implementation and acceptance
- [ ] Define broker-neutral sandbox adapter and order lifecycle state machine
- [ ] Require independent risk authorization before each simulated order
- [ ] Implement idempotency, fill reconciliation, positions and cash ledger
- [ ] Test disconnects, duplicate fills, stale data and emergency halt
- [ ] Keep live order routing disabled until separate explicit approval

## Release constraints
No exchange credentials, live orders, public-node rollout or production claims from this branch. Merge only after actual code, reproducible tests, CI and review.
