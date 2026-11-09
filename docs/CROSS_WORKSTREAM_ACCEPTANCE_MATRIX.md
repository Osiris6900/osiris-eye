# Cross-workstream integration acceptance matrix

> Release-gate status lives in [Integrated delivery gates](../INTEGRATED_DELIVERY_GATES_2026_11_08.md) and `docs/release-gate-status.json`. This file is a scenario list, not the inventory.

Tracking issue: #182. This document is a test plan, **not** evidence that the gates have passed.

| Gate | Scope | Reproducible acceptance evidence | Failure condition |
| --- | --- | --- | --- |
| P2P → ledger | Signed Rust observation, receipt, Python durable ledger | Independent-host test; trace IDs match across receipt and durable ledger after restart | Missing/duplicate receipt, unauthorized publisher accepted, or non-durable acknowledgement |
| E2EE lifecycle | Key rotation, revoked identity, replay handling | Deterministic negative tests and security review attached to #177 | Revoked or replayed payload accepted |
| Market data → research | Authorized source, timestamps, provenance, freshness | Synthetic fixture with delayed, missing, malformed and stale ticks; assertions in #178 | Stale/unentitled data reaches research as valid |
| Research → paper risk | Simulated proposal, independent risk gate, paper order | Tests for exposure caps, position limits, duplicate IDs, restart and kill switch in #179 | Any rejected proposal creates a paper fill |
| Dashboard | Authenticated read-only health and paper metrics | RBAC tests; API contract tests; verify secrets never appear in responses in #180 | Unauthorized user can view privileged state or change controls |
| Partition/recovery | Multi-host outage, relay loss, reconnect | Logs and resource metrics from #176 with repeatable commands | Silent data loss, uncontrolled retry, or unsafe risk state |
| Supply chain | Workflow pinning, locked dependencies, SBOM | Reviewed #167/#171 remediation, advisory report, full CI | New unpinned actions or unresolved release-blocking vulnerabilities |
| Release candidate | End-to-end staging environment | Exact commit SHA, test commands, CI links, threat review, rollback drill | Missing evidence or any failed gate |

## Execution order and ownership

1. Each parallel workstream (#176–#181) maintains its own branch, PR and focused tests.
2. Integration (#182) consumes only reviewed interfaces and pinned commit SHAs; it must not assume a draft PR is merged.
3. Run the matrix on isolated staging infrastructure with synthetic or licensed test data. No exchange credentials or live orders.
4. Capture machine-readable pass/fail output, timestamps, environment, and commit SHAs for every gate.
5. Keep live order routing disabled until every gate passes and independent security, regulatory, and operational approvals are recorded.

## Release evidence template

- Commit SHA:
- Environment and configuration digest (no secrets):
- Test command and CI run URL:
- Scenario/fixture:
- Expected result:
- Actual result:
- Artifacts/log location:
- Reviewer:
- Outcome: PASS / FAIL / NOT RUN

**Current state:** All gates NOT RUN by this document. A PR or checklist alone does not establish release readiness.
