# End-to-end integration acceptance matrix

> Release-gate status lives in [Integrated delivery gates](INTEGRATED_DELIVERY_GATES_2026_11_08.md) and `docs/release-gate-status.json`. This file is a scenario list, not the inventory.

**Status:** planned acceptance tests, not passing evidence. Applies to the exact final release candidate. Related issues: #182, #207, #176–#181. Live execution remains disabled.

| Boundary | Positive case | Mandatory negative/fault case | Retained evidence |
|---|---|---|---|
| Rust P2P → Python ledger | Authenticated encrypted recipient observation is persisted once | Forged receipt, wrong recipient, duplicate/replay and restart | Exact commit, binary SHA-256, redacted receipt/digest and ledger IDs |
| Licensed feed → point-in-time research | Authorized instrument, event and availability timestamps remain intact | Missing rights, stale feed, future knowledge and invalid corporate action | Vendor entitlement attestation, timestamp samples, quality report |
| Research → independent risk | Reproducible approved signal passes risk | Unapproved model, exposure excess, stale feed and kill switch | Model version, input digest, reject/accept reason |
| Risk → paper OMS | One accepted intent yields one deterministic paper order | Retries, process restart, cancel/replace race and malformed order | Order transitions, idempotency key and event journal |
| Paper OMS → accounting | Fill, cash, positions and P&L reconcile | Partial fills, fee mismatch, missing event and restart | Fill ledger, balances, reconciliation diff (zero) |
| Operations → dashboard | Authenticated operator sees current peer/feed/risk state | Unauthorized access, unavailable API and stale status | Role matrix, audit events, redacted screenshots |
| Multi-host P2P | 2/3-host isolation and outage recovery | Relay/bootstrap outage, NAT mismatch and partition | Host-separated run URL, binary hashes, latency/loss/RSS/CPU |
| CI → deploy/restore | Same SHA built, signed, deployed and restored | Dependency advisory, mutable action, broken backup | SBOM, pinned actions, signed artifact and recovery exercise |

## Release evidence rules

1. Record a **full 40-character candidate commit** for every test; never transfer a passing result from an older commit without explicit change-impact review.
2. Retain an HTTPS evidence location and SHA-256 for each artifact; redact addresses, credentials, keys and account data.
3. Negative tests must prove fail-closed behavior; an unavailable test is **blocked**, not passed.
4. An independent reviewer must verify cryptography, security and compliance gates; a self-attested checklist is not an audit.
5. Use `python scripts/check_release_evidence.py manifest.json` for an offline *syntax/completeness* preflight. A green preflight does not authenticate artifacts, attest independence or authorize live orders.
6. Do not mark #207 checkboxes complete until exact-commit CI, evidence and required reviewer signoff are linked.

## Parallel work lanes

- **Lane A:** #171 / #167 / #181 — dependency remediation, immutable CI actions, reproducible builds.
- **Lane B:** #176 / #177 — E2EE, key rotation, multi-host NAT/outage/soak and external audit.
- **Lane C:** #178 / #179 — licensed feeds, reproducible models, independent risk and fill-level paper execution.
- **Lane D:** #180 / #182 — authenticated operator panel, end-to-end integration, observability and recovery.

Merge independent lanes only after their tests pass; production release remains a separate sequential approval gate.
