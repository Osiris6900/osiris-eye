# PR #264 — Market feed integration: implementation and acceptance

## Implemented
- `market_feed_gate.py`: checks authenticated source, entitlement assertion, positive finite price, nonempty instrument/source, monotonic event timestamps, receipt order, and maximum age.
- `test_market_feed_gate.py`: deterministic acceptance and rejection scenarios.
- Existing `phase4_systems.py` provides ISO-8601 normalization, batch auditing, instrument master auditing, and feed health snapshots. These remain separate until explicitly wired.

## Remaining code
1. Create a vendor-neutral adapter interface and an approved-provider configuration with credential isolation; do not imply that a boolean entitlement flag independently proves licensing.
2. Map vendor payloads to normalized point-in-time observations with provider ID, instrument ID, event/receipt/availability times, sequence number, quality and provenance.
3. Implement bounded queues, reconnection, exponential backoff, gap detection, deduplication, clock skew checks, rate limits, heartbeat and failover.
4. Persist append-only observation evidence, source and checksum; expose research-only API read endpoints protected by existing token middleware.
5. Integrate signed P2P provenance and Admin RBAC health status; deny any paper-risk authorization when feed health is unknown, stale or unauthorized.
6. Add contract tests, replay tests, backpressure and failure-injection tests. Verify Python CI, audit licenses and document operating procedures.

## Acceptance
- No unauthorized, unauthenticated, future-dated, stale, duplicate or out-of-order observation enters decision inputs.
- Deterministic replay gives identical normalized results.
- Feed outage and reconnection cannot silently authorize new paper risk.
- External data is licensed and source-approved; no live execution is enabled.
