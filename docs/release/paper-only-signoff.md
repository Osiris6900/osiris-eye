# OSIRIS EYE — paper-only release signoff worksheet (T20)

> **Document state: DRAFT — ALL SIGNOFFS PENDING.**
>
> This worksheet is **not** a release approval, a security certification, a model validation, a regulatory legal opinion, or authorization to submit live broker/exchange orders. A checked-in file, passing unit tests, or successful CI run never constitutes a signoff.

- **Repository:** `Osiris6900/osiris-eye` (AGPL-3.0; independently validate distribution obligations before any release)
- **Document baseline:** `main` at `d9bb0a959faf5d9c7c97bd6502a143f9df9123d0` (PR #324 squash merge)
- **Release posture:** ghost-mode / relay-based networking; no public operator identity in evidence; **only authorized keyless feeds**; **PAPER-ONLY indefinitely**; no broker or exchange live order submission.
- **Tracker:** T20 in `docs/release-gate-status.json` is **Remaining**, `acceptance_satisfied: false`.
- **Scope:** prepare evidence and review decisions for a *paper-only* operating candidate. No live-pilot approval is within this worksheet.

## 1. Decision ledger — every item PENDING

| Domain / gate | Current evidence or known limitation | Required proof before an independent decision | Decision |
| --- | --- | --- | --- |
| T06 cryptographic & operational security review | Issue [#323](https://github.com/Osiris6900/osiris-eye/issues/323) requests an independent review; no signed external assessment accepted | Reviewer organization, agreed scope, signed report/reference, finding severity and remediation disposition | **PENDING** |
| T03 E2EE provenance | [3-host E2EE run 42724204891](https://github.com/Osiris6900/osiris-eye/actions/runs/42724204891); reported artifact `three-e2ee-evidence-42724204891-1` (14-day retention) | Confirm exact candidate SHA, retained artifact bytes and SHA-256, recipient/observer isolation, restart/replay evidence | **PENDING independent review** |
| T04/T19 network capacity and failover | [Capacity workflow](https://github.com/Osiris6900/osiris-eye/actions/workflows/p2p-capacity.yml) exists; PR #324 aligns A/B/C timeouts; no accepted 10/50/100-peer dispatch artifact in this worksheet | Operator-dispatched successful run tied to exact SHA; distinct host fingerprints, peer counts, latency, delivery/loss, outage/recovery, artifact hashes and limitations | **PENDING** |
| T09 feed entitlement and legal posture | Adapter registry includes synthetic fixture; this does not establish real-source rights | Per-source consent/license, permitted usage, redistribution restrictions, timestamp/provenance, revocation tests, reviewer decision | **PENDING** |
| T11/T12 model risk & strategy validation | Walk-forward and cost-adjustment unit tests exist; no accepted model lifecycle archive or independent leakage-adjusted OOS report | Frozen model/feature versions, point-in-time inputs, reproducibility, leakage review, transaction costs, OOS stress results, model owner decision | **PENDING** |
| T13 risk governance | Paper risk limits are tested; no independent budget governance signoff | Limit owners, escalation, gross/net exposure, concentration/drawdown, failure tests, independent review | **PENDING** |
| T14/T15 paper OMS & accounting | Synthetic paper ledger and accounting tests; no live broker integration expected or authorized | Restart/idempotency reconciliation, realistic paper fills/margin/slippage/fees and P&L invariants, exact artifact evidence | **PENDING** |
| T16 fail-closed paper execution | Admin/ingestion kill-switch tests exist; `evaluate_paper_workflow` exposes optional `enforce_execution_controls` and must not be presumed to enforce controls for every caller | Show every paper-order path refuses active kill-switch, stale feed and unavailable risk service; restart recovery; negative E2E tests | **PENDING** |
| T17/T18 operations and DR | Dashboard/RBAC and in-process alerts tested. [#322](https://github.com/Osiris6900/osiris-eye/issues/322) tracks DR; workflow validates recovery plan but does not prove real restore | Authenticated actual-peer telemetry, operator audit events, retained backup/restore/failover logs, RTO/RPO evidence and failure recovery | **PENDING** |
| T20 compliance, security and operational approval | No independent final signoff in the release-gate inventory | Review applicable jurisdictions, privacy and logging, licensing, AGPL distribution, operational controls, remediation and approver authority | **PENDING** |

**Important:** T02, T03 and T05 are described as "Implemented with evidence" in the repository gate inventory; that is **not** proof that T06 independent review or T20 signoff has occurred. Do not treat historical PR/issue closure as release acceptance.

## 2. Minimum evidence record for every claim

Supply all fields below for each real acceptance claim; **PENDING** until verified:

- Gate ID and narrowly scoped acceptance criterion: **PENDING**
- Candidate **exact full commit SHA** (40 hexadecimal characters): **PENDING**
- GitHub Actions **run URL**, workflow name, run attempt and conclusion: **PENDING**
- Job-level results and host topology/runner class where material: **PENDING**
- Retained artifact **exact name**, expiration/retention, content SHA-256 and verification method: **PENDING**
- Test configuration, dataset provenance and failure-injection parameters: **PENDING**
- Negative cases, known limits, unresolved findings and reproduction commands: **PENDING**
- Independent reviewer/approver name, organization, decision and date: **PENDING**

Do not confuse an Actions run ID, commit SHA, artifact filename or GitHub artifact metadata with a **verified SHA-256 of downloaded evidence bytes**. Never invent a digest. Never publish keys, tokens, private identity, operator address or sensitive relay credentials.

## 3. Mandatory safety posture

1. **PAPER-ONLY indefinitely.** All accepted execution paths must leave `broker_order_submitted = false` and `live_execution = false`. This document never authorizes live trading.
2. **Fail-closed.** Unavailable risk service, stale data, active kill-switch, invalid entitlement, unrecognized source and inconsistent state must block new paper-order acceptance.
3. **Ghost-mode.** Disclosed evidence must not reveal a public operator identity, secrets or unnecessary host details.
4. **Keyless authorized inputs only.** Do not infer market data redistribution or usage rights from an adapter passing code tests.
5. **No inferred success.** CI green, mock simulation, local peers, schema validation and unverified workflow runs are not interchangeable with independent real-host evidence.
6. **Independent review gate.** T06 review and any critical/high remediation remain prerequisites for eventual T20 assessment, not formalities.

## 4. Reviewer and decision placeholders — no approval implied

| Role | Reviewer / organization | Scope / report reference | Finding resolution | Decision / date |
| --- | --- | --- | --- | --- |
| Independent cryptography reviewer (T06) | **PENDING** | **PENDING** | **PENDING** | **PENDING** |
| Security/operations reviewer | **PENDING** | **PENDING** | **PENDING** | **PENDING** |
| Market-data licensing/compliance reviewer | **PENDING** | **PENDING** | **PENDING** | **PENDING** |
| Independent model-risk reviewer | **PENDING** | **PENDING** | **PENDING** | **PENDING** |
| Portfolio/paper execution risk owner | **PENDING** | **PENDING** | **PENDING** | **PENDING** |
| Paper-only release decision maker | **PENDING** | **PENDING** | **PENDING** | **PENDING** |

## 5. Gate-state update policy

- This document **does not change** T20, T06, T04, T18 or T19 status.
- Update **both** `docs/release-gate-status.json` **and** its corresponding release-gate Markdown report in a separate evidence PR when actual retained proof has been verified.
- Any future acceptance PR must preserve full SHA/run URL/artifact name/verified digest and explicit scope limitations. A green documentation-only CI check is not acceptance.
- #323 remains open until independent cryptographic review evidence exists; #322 remains an evidence tracker until real restore/DR proof exists.
