# P2P soak and observability — parallel workstream

Status: implementation for rejection throttling merged separately in #256 and regression coverage merged in #259; CI passed on both PR heads. Independent multi-host verification and release evidence remain pending.

## Implementation and acceptance
- [x] Add bounded per-peer rejection log throttling without weakening fail-closed gate (#256, merged)
- [x] Preserve first occurrence and periodic aggregate counts (#256, merged)
- [x] Add unit tests for windows, churn and new unexpected peer (#256, #259, merged; Rust CI passed)
- [ ] Re-run multi-host 100-peer soak with latency, delivery and loss evidence
- [ ] Preserve exact binaries and report hashes

## Verified CI references
- #256 head `163d3e0`: CI run 42462011682, rust/python/web success.
- #259 head `98190f2`: CI run 42463568331, rust/python/web success.
- Neither run is evidence of an independent-host 100-peer soak. Before closing #255, retain post-throttle diagnostics, p50/p95/p99, zero-loss result, distinct host fingerprints, binary SHA-256 and candidate commit SHA. Do not substitute the earlier single-host 1000/1000 result.

## Release constraints
No exchange credentials, live orders, public-node rollout or production claims from this branch. Merge only after actual code, reproducible tests, CI and review.

## Cross-module integration contract
- Cryptography: preserve authenticated source allowlist, E2EE, key revocation and replay protection; throttle logs only, never security decisions (#177, #255).
- AI Research: verify observation freshness/provenance when delivered across hosts (#178).
- Risk Engine: simulated network partition or stale P2P observations must block paper order authorization (#179).
- Admin: RBAC-protected peer metrics, rejection aggregates and alerts; redact identities/secrets where needed (#180).
- Evidence: 2/3-host E2EE and 100-peer sustained soak with signed hashes, latency and loss metrics (#176, #182).
