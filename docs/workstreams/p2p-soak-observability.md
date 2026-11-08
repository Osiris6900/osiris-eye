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
