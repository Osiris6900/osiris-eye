# OSIRIS Eye integration acceptance matrix (draft)

> Release-gate status lives in [Integrated delivery gates](INTEGRATED_DELIVERY_GATES_2026_11_08.md) and `docs/release-gate-status.json`. This file is a scenario list, not the inventory.

Tracking: #182. This document is an initial auditable checklist, not evidence of passing tests.

| Gate | Test scenario | Pass criteria | Evidence |
| --- | --- | --- | --- |
| P2P | two independent hosts exchange authorized observations | authenticated peer identity, bounded reconnection, no unauthorized messages | pending |
| E2EE | revoked publisher sends an encrypted envelope | rejection before decrypt or replay-cache mutation | pending |
| Receipt bridge | signed receipt travels from Rust verifier to durable Python ledger | exact message/recipient/digest correlation, persistence before acknowledgement | pending |
| Fault recovery | duplicate receipt, lost receipt, restart, network partition | idempotent state; never mark local send as remote admission | pending |
| Market data | stale, malformed or untrusted observation | reject before research/paper execution | pending |
| Paper execution | position limit, kill switch, restart and duplicate order | fail closed; no live order routing | pending |
| Dashboard | unauthorized request to operational endpoints | access denied; no secrets in response | pending |
| Release | dependency advisories and workflow action pinning | resolve #171 and #167 with verified CI | pending |

## Verification rules

- Link each row to a test command, CI run URL, commit SHA, and actual outcome before marking passed.
- Distinguish loopback/unit tests from independent-host tests.
- Live order routing remains disabled until all release gates have independently verified evidence.
- Do not merge an implementation without green relevant tests and review.
