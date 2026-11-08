# Phase 07: Advanced Risk Management

Tracking: #207. Parallel workstream kickoff; this specification does **not** represent a completed implementation.

## Scope
Independent pre-trade gates, exposure and margin limits, stress testing, portfolio drawdown ceilings, emergency halt and audit trails.

## Acceptance evidence
Risk fail-closed, emergency halt and stress integration tests.

## Integration rules
- Use versioned typed interfaces and traceable event IDs shared with existing services.
- Separate synthetic, paper, sandbox and live environments; live routing is not enabled by this document.
- Retain exact commit SHA, CI logs, test artifacts, reviewer and unresolved limitations before merge.
- Coordinate changes to shared contracts with Phase 1–5 branches and master tracker #207.
