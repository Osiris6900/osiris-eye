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

## Cross-module integration contract
- Cryptography: record authenticated signed intents/receipts without interpreting unverified peer messages as executable orders (#177).
- AI Research: only approved, versioned strategies can emit paper intents; attach model/data timestamps (#178).
- Risk Engine: independent fail-closed authorization, idempotent order IDs, kill-switch, position/cash reconciliation and recovery (#179).
- Admin: RBAC read-only order/risk status and authorized emergency halt with audit events (#180).
- Evidence: deterministic replay, duplicate fills, reconnect, partition and emergency halt tests (#182). No live order routing.
