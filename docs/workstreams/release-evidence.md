# Release evidence and deployment — parallel workstream

Status (2026-11-14): release tooling is implemented; independent acceptance is
pending. Reviewed baseline: `46c3b61687fcec8ee73f7e0386c3d02b8ac838db`.
The inventory remains **3 evidenced / 14 Partial / 3 Remaining**. Historical
accepted rows do not certify a new release candidate.

## Evidence preflight hardening

`scripts/check_release_evidence.py` previously allowed an evidence author to
omit or disable `independent_review_required` for every gate. Independent
crypto, source rights, model lifecycle, OOS strategy validation, portfolio risk
and compliance decisions now always require literal `independent_reviewed=true`.
Optional review declarations must also be real JSON booleans. This closes an
opt-out in reference validation; a declaration still does not prove who reviewed
an artifact, validate a signature or fetch the original evidence bytes.

Every result preserves `release_accepted=false`,
`independent_review_verified=false` and `live_trading_authorized=false`.
`ready=true` means only that the reference preflight is complete. Do not copy
test-fixture approvals into a real evidence record.

Core CI and both `verify_all.py` profiles run the release-evidence contract
tests. The engineering profile now has 31 checks, including this contract suite
and the actual local paper API process/backup/outage/recovery exercise;
passing it does not promote any release gate.

Reproduce the focused checks with the repository's development dependencies:

```bash
python -m unittest discover -s scripts -p 'test_check_release*.py' -v
python scripts/test_verify_all.py
python scripts/check_release_gate_status.py
python scripts/check_release_gate_status.py --strict
```

The last command must fail while T01, T04, T06–T20 remain unaccepted. The default
inventory check verifies internal consistency and is expected to pass. Full
engineering verification remains `python scripts/verify_all.py --profile
engineering --execute --report /tmp/osiris-engineering-unique.json`.

## Evidence still required

The [machine-readable inventory](../release-gate-status.json) retains the
specific blocker for every gate. The [updated signoff worksheet](../release/paper-only-signoff.md)
distinguishes implemented controls and historical artifacts from pending decisions.

| Gates | Required acceptance work |
| --- | --- |
| T01 | Final-candidate dependency audit, downloaded SBOM/provenance and reviewed `paste` maintenance disposition. |
| T04 | Sustained independent-host NAT/relay/open-mesh and traffic-on outage/recovery, with verified topology and retained bytes. |
| T06 | Signed independent cryptography/operational-security findings and remediation retest. |
| T07–T10 | Authorized multi-asset/instrument and genuine streaming/event captures, quality/freshness/corporate-action evidence and original per-source rights decisions. |
| T11–T12 | Frozen genuine data/model/features, reproducible blinded OOS and independent leakage/cost/turnover validation. |
| T13 | Independently approved risk budgets, limit ownership, escalation and exception controls. |
| T14–T15 | Final-candidate paper reconciliation, authorized sandbox comparison and calibrated execution/fee/margin/P&L evidence. |
| T16 | Independent service-level fault injection for kill-switch, stale/unavailable feed, unavailable risk and recovery across all paper paths. |
| T17 | Sustained authenticated actual-peer monitoring, audit/alert delivery and operational acceptance. |
| T18 | Approved independent-host running-service backup/restore/failover, measured RTO/RPO and accepted evidence custody. |
| T19 | Final-candidate capacity/failover plus verified receipt → durable ledger → risk → paper order tracing and independent review. |
| T20 | Actual independent security/model/risk/licensing/compliance decisions and authorized paper-only operator signoff/rollback. |

No external review, entitlement, host or operator decision is supplied by this
tooling change. Preserve the existing inventory statuses until actual evidence
is verified and accepted in both JSON and Markdown.

## Implementation and acceptance
- [ ] Require immutable action references and dependency provenance
- [ ] Generate and validate Rust/npm SBOM; resolve Python deployment lock
- [ ] Verify staging smoke tests, backups, restore and failover
- [ ] Require linked cryptography review and paper-only end-to-end evidence
- [ ] Block production release when any gate is missing

## Release constraints
No exchange credentials, live orders, public-node rollout or production claims from this branch. Merge only after actual code, reproducible tests, CI and review.

## Cross-module integration contract
- Cryptography: retain independent review, revocation/replay tests, OSV remediation and exact binary hashes (#171, #177).
- AI Research: reproducible data provenance, walk-forward reports, leakage/cost/drift checks and approved model versions (#178).
- Risk Engine: demonstrate independent kill-switch, stale-feed block, journal reconciliation and recovery (#179).
- Admin: demonstrate authenticated RBAC, audit/redaction, monitoring, restore and operator incident drills (#180).
- Evidence: stage-by-stage CI gates, SBOM, immutable action pinning, staging smoke tests and cross-module release matrix (#181, #182, #207). Live execution requires separate approval.
