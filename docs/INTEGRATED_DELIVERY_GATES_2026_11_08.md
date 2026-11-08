# Integrated Delivery Gates — 2026-11-08

Tracking issue: https://github.com/Osiris6900/osiris-eye/issues/207

This plan coordinates independent workstreams in parallel. A checked-off plan is not implementation evidence. No live broker or exchange order routing is authorized by this document.

## Workstreams and acceptance

| Stream | Priority | Required proof |
| --- | --- | --- |
| Dependency and workflow supply chain | P0 | Close #171 and #167 only after exact advisory reports, regression tests, immutable action pins, and reviewed CI |
| P2P cryptography | P0 | Exact candidate two-/three-host E2EE, replay, revocation, outage and load tests; retained SHA256 binary hashes and logs |
| Public-node operations | P0 | Operator-controlled bootstrap/relay deployment, network connectivity, health, recovery and independent security review |
| Market data | P1 | Authorized adapters, feed quality, entitlements, UTC/event-time provenance, point-in-time validation |
| Quant and AI research | P1 | Reproducible training, leakage-safe walk-forward, out-of-sample diagnostics, costs, model drift and governance |
| Execution and paper trading | P1 | Sandbox OMS, idempotent orders, realistic fills, reconciliation, independent risk gate and kill switch |
| Operator console | P2 | Authenticated peer, feed, strategy, risk, order and audit controls with emergency halt |
| Release operations | P0 | Observability, backup, failover, capacity, security, compliance and model-risk signoff |

## Parallelization rules

- Security/P2P, market-data, AI research, execution simulator, and dashboard can be developed in separate feature branches.
- Define shared contracts before integrating cross-stream components.
- Keep synthetic and authorized paper-trading environments separate from any live account.
- Every PR must link an issue, contain test instructions, and state its evidence boundary.
- No production deployment is inferred from successful local tests or documentation.

## Release gates

1. **G0 — Reproducible build:** exact revision, clean CI, pinned dependencies/actions, vulnerability and license reviews.
2. **G1 — P2P acceptance:** final-candidate independent-host encrypted exchange, recipient isolation, replay/revocation, NAT/relay and sustained failure/load tests.
3. **G2 — Data acceptance:** verified rights, provenance, freshness, point-in-time correctness and outage controls.
4. **G3 — Research acceptance:** documented walk-forward and untouched out-of-sample evaluation with costs and risk.
5. **G4 — Paper acceptance:** reconciled fills, cash, positions, daily limits, independent kill switch and recovery drills.
6. **G5 — Operational review:** monitoring, incident response, disaster recovery, external security and applicable compliance review.
7. **G6 — Live pilot (separate authorization):** only after all preceding gates and explicit operator approval; never an automatic consequence of merging code.

## Evidence log template

For each gate retain: issue/PR, commit SHA, test environment, commands, timestamp, expected/actual result, sanitized artifact links, reviewer, unresolved limitations and decision.

## Current known blockers

- https://github.com/Osiris6900/osiris-eye/issues/171 — Cargo advisories and unresolved Python dependency gate.
- https://github.com/Osiris6900/osiris-eye/issues/167 — mutable GitHub Actions references.
- Public deployment, sustained final-candidate multi-host acceptance and independent audit are not yet established by the existing P2P release-gate document.
