# OSIRIS EYE — paper-only release signoff worksheet (T20)

> **Document state: DRAFT — ALL SIGNOFFS PENDING.**
>
> This worksheet is **not** a release approval, a security certification, a model validation, a regulatory legal opinion, or authorization to submit live broker/exchange orders. A checked-in file, passing unit tests, or successful CI run never constitutes a signoff.

- **Repository:** `Osiris6900/osiris-eye` (AGPL-3.0; independently validate distribution obligations before any release)
- **Document baseline:** refreshed 2026-11-14 against `main` at `46c3b61687fcec8ee73f7e0386c3d02b8ac838db`. Implementation references below describe that checkout; historical evidence retains its original candidate SHA and limitations.
- **Release posture:** ghost-mode / relay-based networking; no public operator identity in evidence; **only authorized keyless feeds**; **PAPER-ONLY indefinitely**; no broker or exchange live order submission.
- **Tracker:** T20 in `docs/release-gate-status.json` is **Remaining**, `acceptance_satisfied: false`.
- **Scope:** prepare evidence and review decisions for a *paper-only* operating candidate. No live-pilot approval is within this worksheet.

## 1. Decision ledger — every item PENDING

| Domain / gate | Current evidence or known limitation | Required proof before an independent decision | Decision |
| --- | --- | --- | --- |
| T06 cryptographic & operational security review | Issue [#323](https://github.com/Osiris6900/osiris-eye/issues/323) requests an independent review; no signed external assessment accepted | Reviewer organization, agreed scope, signed report/reference, finding severity and remediation disposition | **PENDING** |
| T03 E2EE provenance | [3-host E2EE run 42724204891](https://github.com/Osiris6900/osiris-eye/actions/runs/42724204891); reported artifact `three-e2ee-evidence-42724204891-1` (14-day retention) | Confirm exact candidate SHA, retained artifact bytes and SHA-256, recipient/observer isolation, restart/replay evidence | **PENDING independent review** |
| T04/T19 network capacity and failover | Inventory records [capacity run 42740964830](https://github.com/Osiris6900/osiris-eye/actions/runs/42740964830) at `d9bb0a959faf5d9c7c97bd6502a143f9df9123d0`, retained `capacity-report-42740964830-1`, downloaded ZIP SHA-256 `229553051305aabbc28c44af50bb58c4f6dfe9ec746ff3f2f85178333661d239`. Its 10/50/100-peer sweeps each delivered 200/200, but lasted only 35–49 seconds on one tailnet in a recipient-bootstrap star; traffic paused during outage and host independence is self-attested. [Recorded scope and byte digests](../INTEGRATED_DELIVERY_GATES_2026_11_08.md#addendum--2026-11-10-verified-operator-dispatched-1050100-peer-capacity-evidence). Local receipt-to-paper process-loss/recovery is also implemented in [`run_receipt_paper_acceptance.py`](../../scripts/run_receipt_paper_acceptance.py). | Final-candidate sustained independent-host NAT/relay/open-mesh and traffic-on outage/recovery evidence, independently verified topology, actual downloaded artifact hashes and operational acceptance | **PENDING** |
| T09 feed entitlement and legal posture | [`authorized_feeds.py`](../../services/quant-api/src/osiris_api/authorized_feeds.py) implements source-governed intake; [`check_vendor_entitlement_evidence.py`](../../scripts/check_vendor_entitlement_evidence.py) validates evidence intake. Public Coinbase paper-path support and source declarations do not establish independent rights approval or redistribution permission. | Per-source consent/license, permitted usage, redistribution restrictions, timestamp/provenance, revocation tests, reviewer decision | **PENDING** |
| T11/T12 model risk & strategy validation | Point-in-time features, ridge fitting and immutable experiment reproduction exist in [`control_plane/models.py`](../../services/quant-api/src/osiris_api/control_plane/models.py) and [`control_plane/experiments.py`](../../services/quant-api/src/osiris_api/control_plane/experiments.py). [`record_model_research_evidence.py`](../../scripts/record_model_research_evidence.py) produces a reproducible synthetic-only walk-forward archive with approvals pending; [retained engineering evidence](VERIFIED_ARTIFACTS_2026_11_12.json) is recorded separately from independent acceptance. | Frozen genuine model/feature/data versions, blinded OOS inputs, independent leakage review, calibrated costs/turnover/impact/borrow sensitivities, reproducibility and model owner decision | **PENDING** |
| T13 risk governance | [`risk.py`](../../services/quant-api/src/osiris_api/risk.py) and [`paper_execution.py`](../../services/quant-api/src/osiris_api/paper_execution.py) implement canonical limits, fresh-mark exposure and adverse unrealized-loss checks; independent budget governance signoff remains absent. | Limit owners, escalation, gross/net exposure, concentration/drawdown, failure tests, independent review | **PENDING** |
| T14/T15 paper OMS & accounting | The durable canonical paper ledger implements order/fill state, partial fills, cancellation, idempotency, reconciliation, fees/P&L and process recovery. The [connected market-to-paper harness](../PAPER_PIPELINE_ACCEPTANCE.md) exercises actual code with offline candles and offers a separate public-data exercise; configurable adverse slippage is a sensitivity assumption. No accepted broker-sandbox match or calibrated venue execution is established. | Final-candidate reconciliation/artifact review, authorized paper-broker comparison, venue-calibrated queue/liquidity/impact/margin/borrow and realistic fill/cost evidence | **PENDING** |
| T16 fail-closed paper execution | [`evaluate_paper_workflow`](../../services/quant-api/src/osiris_api/paper_workflow.py) now defaults execution controls on and refuses approval when they are disabled or required evidence is missing. [`submit_approved_workflow`](../../services/quant-api/src/osiris_api/paper_execution.py) binds the intent and rechecks current canonical risk; [`run_paper_failure_exercise.py`](../../scripts/run_paper_failure_exercise.py) exercises real code with disposable synthetic SQLite. These are engineering controls and fixtures. | Independent final-candidate negative E2E/service-outage acceptance covering every paper-order path, kill-switch, stale/unavailable feeds, unavailable risk, restart and retained artifacts | **PENDING** |
| T17/T18 operations and DR | [Local authenticated peer telemetry](../P2P_LOCAL_ADMIN_TELEMETRY.md), dashboard/RBAC and audit/alert controls exist. [`run_paper_dr_exercise.py`](../../scripts/run_paper_dr_exercise.py) performs actual temporary SQLite backup, destruction, restore and continuation; [`check_paper_cross_runner_restore.py`](../../scripts/check_paper_cross_runner_restore.py) checks artifact portability, and [`verify_cross_runner_dr_artifacts.py`](../../scripts/verify_cross_runner_dr_artifacts.py) validates retained evidence. These do not certify independent-host running-service recovery. [#322](https://github.com/Osiris6900/osiris-eye/issues/322) remains the DR tracker. | Sustained actual-peer monitoring and alert delivery; approved independent-host backup/restore/failover with accepted custody/storage, measured RTO/RPO, final-candidate logs and operator decision | **PENDING** |
| T20 compliance, security and operational approval | No independent final signoff in the release-gate inventory | Review applicable jurisdictions, privacy and logging, licensing, AGPL distribution, operational controls, remediation and approver authority | **PENDING** |

**Important:** T02, T03 and T05 are described as "Implemented with evidence" in the repository gate inventory; that is **not** proof that T06 independent review or T20 signoff has occurred. Do not treat historical PR/issue closure as release acceptance.

This refresh changes no acceptance decisions: the baseline inventory remains
**3 Implemented with evidence / 14 Partial / 3 Remaining**, with seventeen
unaccepted gates. Evidence digests above are attributed to the existing inventory;
this document refresh does not claim a new artifact download or a new test run.
[`scripts/verify_all.py`](../../scripts/verify_all.py) maps engineering checks to
gates; its engineering profile does not replace strict release acceptance.

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
