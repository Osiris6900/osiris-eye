# P2P soak and observability — parallel workstream

Status: started (branch and acceptance checklist created); implementation and verification are pending.

## Implementation and acceptance
- [ ] Add bounded per-peer rejection log throttling without weakening fail-closed gate
- [ ] Preserve first occurrence and periodic aggregate counts
- [ ] Add unit tests for windows, churn and new unexpected peer
- [ ] Re-run multi-host 100-peer soak with latency, delivery and loss evidence
- [ ] Preserve exact binaries and report hashes

## Release constraints
No exchange credentials, live orders, public-node rollout or production claims from this branch. Merge only after actual code, reproducible tests, CI and review.

## Cross-module integration contract
- Cryptography: preserve authenticated source allowlist, E2EE, key revocation and replay protection; throttle logs only, never security decisions (#177, #255).
- AI Research: verify observation freshness/provenance when delivered across hosts (#178).
- Risk Engine: simulated network partition or stale P2P observations must block paper order authorization (#179).
- Admin: RBAC-protected peer metrics, rejection aggregates and alerts; redact identities/secrets where needed (#180).
- Evidence: 2/3-host E2EE and 100-peer sustained soak with signed hashes, latency and loss metrics (#176, #182).
