# Integrated Delivery Gates — verified inventory 2026-11-08

Tracking issues: [#207](https://github.com/Osiris6900/osiris-eye/issues/207) and [#182](https://github.com/Osiris6900/osiris-eye/issues/182).

Baseline commit: `5176eae8843a1e5144ac903f37843fe9bb46cdd9`.

Passing CI on that commit:

- [CI 42494850551](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850551) — python, rust, and web jobs success.
- [Dependency Security Audit 42494850772](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850772) — dependency-inventory, python-audit, npm-audit, and rust-audit success.
- [Tailscale cross-host 42495167275](https://github.com/Osiris6900/osiris-eye/actions/runs/42495167275) — Host A and Host B success, `CROSS_HOST_P2P_STRESS=PASS`. This is a two-runner observation exchange. It is not a 2/3-host E2EE run and it does not retain node binary hashes.
- [Three-peer mesh 42495167271](https://github.com/Osiris6900/osiris-eye/actions/runs/42495167271) — one runner, success.

Issue #207 still has 0 of 20 checkboxes ticked. This inventory does not edit that issue. Live trading is not authorized.

Status rule: **Implemented with evidence** requires a merged commit and a passing CI run, or an attached artifact, that meets the checkbox. Merged code whose CI run does not meet the checkbox is **Partial**. A missing run or artifact is **Remaining**.

Machine-readable copy: [`docs/release-gate-status.json`](release-gate-status.json). Recheck with `python scripts/check_release_gate_status.py`. `--strict` fails while any row is Partial or Remaining. CI runs the default mode.

## Status table

| Tracker item | Module issue | Status | Evidence | Open PRs | Missing evidence / blocker |
| --- | --- | --- | --- | --- | --- |
| T01. Remediate OSV dependencies (#171) and resolve Python lock/provenance gate | #171 | Partial | [PR #239](https://github.com/Osiris6900/osiris-eye/pull/239) and [PR #257](https://github.com/Osiris6900/osiris-eye/pull/257) merged. [Audit 42494850772](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850772) green at `5176eae`. [`docs/P2P_DEPENDENCY_ADVISORIES.json`](P2P_DEPENDENCY_ADVISORIES.json) queried 2026-11-08T11:20:57Z at source `587dbc71`: 0 vulnerability groups; lockfile hashes match this worktree. Hickory 0.26.3 and yamux 0.14.1 are the only copies in `services/p2p-node/Cargo.lock`. Hashed lock: `services/quant-api/requirements-lock.txt`. | #276 | paste 1.0.15 (RUSTSEC-2024-0436) remains. `scripts/generate_dependency_sbom.py` still emits `osiris:python-deployment-resolution=unresolved`. `scripts/check_license_provenance.py` is not a Dependency Security Audit step. The #171 issue text still describes the pre-remediation advisories and an unresolved Python lock. |
| T02. Implement immutable action pinning and CI ratchet (#167) | #167 | Implemented with evidence | [PR #240](https://github.com/Osiris6900/osiris-eye/pull/240) merged as `6c6d155110c796d43bf5c879ea1487e1620ac81a`. [CI 42494850551](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850551) python job runs `scripts/check_action_pinning.py` and succeeded at `5176eae`. Nine workflow files contain 68 `uses` references, all 40-character SHAs, zero mutable tags. | — | Checkbox evidence is the SHA ratchet on CI. A separate trust review of those pinned commits remains on T06. |
| T03. Re-run final-candidate 2/3 host E2EE with binary hashes and retained evidence | #176, #177 | Remaining | Two-host [run 42424830531](https://github.com/Osiris6900/osiris-eye/actions/runs/42424830531) and three-host [run 42424833409](https://github.com/Osiris6900/osiris-eye/actions/runs/42424833409) succeeded at `154a651`. Three-host [run 42430583187](https://github.com/Osiris6900/osiris-eye/actions/runs/42430583187) succeeded again at `bd56475`. [`docs/P2P_REMOTE_ACCEPTANCE_2026_11_07.json`](P2P_REMOTE_ACCEPTANCE_2026_11_07.json) records payload digests and states binary hashes were not retained. The two-host workflow [run 42430580795](https://github.com/Osiris6900/osiris-eye/actions/runs/42430580795) failed on Host A at `bd56475`. None of these runs are `5176eae`. | #270, #274 | No final-candidate 2/3-host E2EE run on `5176eae` retains node binary SHA-256. |
| T04. Run sustained multi-host outage/load/relay/NAT acceptance | #176, #255 | Remaining | [`docs/P2P_SCALE_REGRESSION_2026_11_08.json`](P2P_SCALE_REGRESSION_2026_11_08.json) is a dirty 20-peer single-host summary with `independent_hosts_verified: false`. [`docs/P2P_ADMISSION_LOCAL_100_REGRESSION.json`](P2P_ADMISSION_LOCAL_100_REGRESSION.json) is `single_host_process_regression`. Log throttle merged in [PR #256](https://github.com/Osiris6900/osiris-eye/pull/256) and [PR #259](https://github.com/Osiris6900/osiris-eye/pull/259); rust CI on `5176eae` is green. | #269, #279 | 100-peer independent-host soak with distinct fingerprints, binary hashes, latency, and loss is not retained. |
| T05. Deploy operator-controlled public nodes with observability and incident recovery | #176, #181 | Remaining | Deployment notes in [`docs/P2P_PUBLIC_NODE_DEPLOYMENT.md`](P2P_PUBLIC_NODE_DEPLOYMENT.md). | — | No operator deployment, uptime, or incident-recovery record is attached to a commit. |
| T06. Commission independent cryptography and operational security review | #177, #181 | Remaining | `scripts/check_master_integration.py` defines the review shape and is not a passing CI gate. | #270 | No independent review with reviewer, organization, scope, and an approved decision is checked in. |
| T07. Build authorized multi-asset vendor adapters and instrument master | #178 | Partial | [PR #243](https://github.com/Osiris6900/osiris-eye/pull/243) merged. Quant API CI [run 42467036806](https://github.com/Osiris6900/osiris-eye/actions/runs/42467036806) succeeded at `30f5f2a`. Main pytest again in [CI 42494850551](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850551). Code: `services/quant-api/src/osiris_api/authorized_feeds.py`, `instrument_master.py`. | #264 | The adapter shipped on main is a synthetic fixture. Its module docstring states that no vendor licence is claimed. |
| T08. Implement streaming, point-in-time normalization, feed freshness and corporate actions | #178 | Partial | Freshness and admission code is covered by pytest in [CI 42494850551](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850551). | #264 | No retained streaming capture or corporate-action vendor file is attached. |
| T09. Verify feed entitlements and redistribution rights | #178 | Remaining | Entitlement fields are fail-closed in `authorized_feeds.py` and `feed_admission.py`. | #264 | Feed licence signoff is absent. A fail-closed check is not a verification of redistribution rights. |
| T10. Integrate global event and macro feeds with source provenance | #178 | Partial | [PR #261](https://github.com/Osiris6900/osiris-eye/pull/261) merged. Tests on main run in [CI 42494850551](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850551). | — | Offline event validation is merged. No provenance attestation for a real macro or event feed is attached. |
| T11. Implement reproducible feature/model lifecycle and walk-forward gates | #178 | Partial | Walk-forward tests in `services/quant-api/tests/test_walk_forward_178.py`, [PR #243](https://github.com/Osiris6900/osiris-eye/pull/243), CI [run 42467036806](https://github.com/Osiris6900/osiris-eye/actions/runs/42467036806). | #273 | No retained feature/model lifecycle archive for a candidate model is attached. |
| T12. Validate strategies out of sample after all costs and leakage checks | #178 | Partial | `test_walk_forward_178.py` runs in [CI 42494850551](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850551) and checks a cost adjustment. | #273 | No out-of-sample strategy report with a leakage review is attached. |
| T13. Build independent portfolio risk budgets and strategy governance | #179 | Partial | `services/quant-api/src/osiris_api/risk.py` and `tests/test_paper_risk_acceptance_179.py`. [PR #258](https://github.com/Osiris6900/osiris-eye/pull/258) merged. Rust risk kernel and pytest pass in [CI 42494850551](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850551). | #268, #277 | Simulation limits are tested. No independent risk-budget governance signoff is attached. |
| T14. Implement broker sandbox adapters, OMS state machine, idempotency and reconciliation | #179 | Partial | Synthetic idempotent fills in `durable_paper_ledger.py`, tested by `tests/test_durable_paper_ledger.py`. Quant API CI [run 42460392297](https://github.com/Osiris6900/osiris-eye/actions/runs/42460392297) at `305ceb1` and main [CI 42494850551](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850551). | #265, #277 | Broker OMS reconciliation is missing. #277 adds an internal paper book and `reconcile()`, still with `broker_order_submitted: false`. `scripts/paper_order_journal.py` is an offline journal and is not a CI step. |
| T15. Add realistic paper fills, positions, cash, margin and P&L accounting | #179 | Partial | The same durable-ledger tests assert cash, position quantity, and realized P&L in [CI 42494850551](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850551). | #265, #277, #278 | Fills are synthetic. Margin is not modeled. #278 only displays stored fills. |
| T16. Prove kill-switch and stale-feed fail-closed behavior | #179, #180 | Partial | Admin kill-switch API tests in `tests/test_admin_api_180.py`. [PR #245](https://github.com/Osiris6900/osiris-eye/pull/245), Quant API CI [run 42471703916](https://github.com/Osiris6900/osiris-eye/actions/runs/42471703916) at `0a610db`. Paper-journal coverage lives in `scripts/test_paper_order_journal.py`. | #265, #268, #277 | On main, `evaluate_paper_workflow` does not consult a kill switch or feed freshness. #277 would add that on its own paper path and is not merged. The paper-journal test is not a CI step. |
| T17. Build authenticated operator dashboard for peers, feeds, orders, risk and audit | #180 | Partial | [PR #245](https://github.com/Osiris6900/osiris-eye/pull/245) merged. Quant API CI [run 42471703916](https://github.com/Osiris6900/osiris-eye/actions/runs/42471703916) and web job in [CI 42494850551](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850551). Code: `admin_centre.py`, `apps/web/src/AdminCentre.tsx`. | #271, #278 | RBAC tests passed. #278 still sets `peer_count` to null. Peer telemetry against a running p2p-node is not in the merged CI runs. #180 is still open. |
| T18. Add metrics, alerts, distributed persistence, backups and disaster recovery | #181, #180 | Partial | In-process alert registry covered with the admin API tests in [run 42471703916](https://github.com/Osiris6900/osiris-eye/actions/runs/42471703916). | #272 | No backup, restore, or disaster-recovery exercise is retained. |
| T19. Run capacity/failover and CI release gates with traceable artifacts | #181, #182 | Partial | [CI 42494850551](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850551), [audit 42494850772](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850772), [Tailscale 42495167275](https://github.com/Osiris6900/osiris-eye/actions/runs/42495167275), and [mesh 42495167271](https://github.com/Osiris6900/osiris-eye/actions/runs/42495167271) succeeded at `5176eae`. | #269, #272, #274 | Traceable CI exists. Independent-host capacity and failover artifacts do not. |
| T20. Perform compliance, model-risk and security signoff before any limited live pilot | #182, #207 | Remaining | No signoff artifact. | #273, #274 | No compliance, model-risk, or security signoff is attached. Live trading stays unauthorized. |

Counts on this baseline: 1 Implemented with evidence, 13 Partial, 6 Remaining.

## Open gate PRs

Reviewed against the pull request diffs on 2026-11-08. `main` is still `5176eae`. None of these PRs are merged, and none had a review. A green check on an open head is not acceptance evidence for #207. CI for #276–#279 was still running or not fully reported when this revision was written. None of these merges, by themselves, move a row to Implemented with evidence.

| PR | State | #207 rows | What merging alone produces |
| --- | --- | --- | --- |
| [#264](https://github.com/Osiris6900/osiris-eye/pull/264) market feeds | Open, not draft, unreviewed. Checks: python, test, rust, web success. | T07, T08, T09 | Scaffolding. `StreamingFeedGate` is an offline validator. Its module text says it performs no network connection. The PR body says the branch is planning only, and the same body checks boxes for licensed streaming adapters. Those boxes overclaim the diff. |
| [#265](https://github.com/Osiris6900/osiris-eye/pull/265) broker sandbox | Draft, unreviewed. Dependency audits and CI success. Touches `main.py`. | T14, T15, T16 | Scaffolding. `authorize_paper_order` returns a reason string and its module text says it does not connect to brokers or place orders. It does not reconcile OMS state. The checked acceptance boxes in the body overclaim that. |
| [#268](https://github.com/Osiris6900/osiris-eye/pull/268) risk boundary tests | Draft, unreviewed. CI success. One new test file. | T13, T16 | Scaffolding. Extra unit cases for invalid inputs and boundaries. No governance signoff and no proof that the paper workflow fails closed on a kill switch. |
| [#269](https://github.com/Osiris6900/osiris-eye/pull/269) soak classification | Draft, unreviewed. CI success. | T04, T19 | Scaffolding, and largely duplicate. `scripts/test_run_p2p_soak.py` already exercises `measurement_class` and is a step in CI run 42494850551. `scripts/test_p2p_multihost_classification.py` is already on main. The new file is not named by `.github/workflows/ci.yml`, so merging it does not add a CI execution of that file and does not create a 100-peer soak. |
| [#270](https://github.com/Osiris6900/osiris-eye/pull/270) receipt replay gate | Draft, unreviewed. CI success. | T03, T06 | Scaffolding. An isolated fail-closed function plus tests. It does not re-run 2/3-host E2EE and does not retain binary hashes. |
| [#271](https://github.com/Osiris6900/osiris-eye/pull/271) health projection | Draft, unreviewed. CI success. | T17 | Scaffolding. An isolated health primitive beside the admin centre already merged in #245. It does not show the dashboard reading a live p2p-node. |
| [#272](https://github.com/Osiris6900/osiris-eye/pull/272) staging config | Draft, unreviewed. CI success. | T18, T19 | Scaffolding. A fail-closed config check. It is not a backup, restore, or failover drill. |
| [#273](https://github.com/Osiris6900/osiris-eye/pull/273) out-of-sample eligibility | Draft, unreviewed. CI success. | T11, T12, T20 | Scaffolding. `evaluate_model` is an offline predicate. It is not a retained out-of-sample strategy report and it is not model-risk signoff. |
| [#274](https://github.com/Osiris6900/osiris-eye/pull/274) candidate and host binding | Draft, unreviewed. CI success. | T03, T04, T19, T20 | Scaffolding. `check_candidate_binding` requires two distinct non-empty fingerprint strings. It does not call `measurement_class`, so two labels from one machine still pass. Hold it until it uses the soak classifier. |
| [#276](https://github.com/Osiris6900/osiris-eye/pull/276) Hickory/Yamux lock floor | Open, not draft, unreviewed. `Cargo.lock` unchanged. | T01 | Scaffolding. Rejects locked Hickory below 0.26.1/0.26.2 and Yamux below 0.13.10, and wires that check into Dependency Security Audit. Prints paste as `REVIEW` and does not fail on it. Does not clear T01. |
| [#277](https://github.com/Osiris6900/osiris-eye/pull/277) paper kill switch and exposure | Open, not draft, unreviewed. Copies the #268 test file unchanged. | T13, T14, T15, T16 | Code, not acceptance, until it merges and CI passes. Adds a paper book with kill switch, stale-data refusal, and an internal `reconcile()`. `broker_order_submitted` stays false. It is not a broker OMS. |
| [#278](https://github.com/Osiris6900/osiris-eye/pull/278) admin fills and outbox | Open, not draft, unreviewed. | T15, T17 | Read-only display of stored synthetic fills and outbox counts. `open_orders` is null. `peer_count` is null. Does not prove peer telemetry. |
| [#279](https://github.com/Osiris6900/osiris-eye/pull/279) rejection-log throttle | Open, not draft, unreviewed. | T04 | Adjusts `peer_not_allowed` log lines and soak parsing. The allowlist still drops the message. It does not run a 100-peer independent-host soak. |

## Recommended merge order

1. **#276** (#171). Lock-floor ratchet only. `Cargo.lock` stays as it is. Paste and the SBOM marker keep T01 Partial.
2. **#279** (#255, #176) before any new soak run, so the soak parses the throttle lines this PR emits. It does not close T04.
3. **#277** (#179) ahead of #268. It already contains that test file unchanged. It is the paper-path kill switch and stale-data work. T14 and T16 stay Partial until the merge commit's CI is green, and T14 still lacks a broker.
4. **#268** only if #277 is not merged. Merging both will touch the same new test file.
5. **#278** (#180) before #271. Read-only fills and outbox status. `peer_count` stays null, so T17 stays Partial.
6. **#272** (#181). Isolated staging-config script. It does not close T18.
7. **#270** (#177) only after a reviewer checks it against the existing receipt and revocation path.
8. **#271** (#180) after #278, and only if it uses the `admin_centre` permission tables.
9. **#273** (#178) as an eligibility function. The merge note should say it leaves T12 Partial.
10. **#264** (#178) as an offline feed validator. The merge note should say it leaves T09 Remaining.
11. **Hold #274** (#182) until host binding uses `measurement_class` from `scripts/run_p2p_soak.py`.
12. **#265 last** (#179). Read it beside both `durable_paper_ledger.py` and #277's `paper_execution.py` so two paper books are not merged blindly. It does not create broker OMS reconciliation.
13. **#269 optional** (#176, #255). Equivalent classification cases already run in CI.

## Evidence still missing

These five items block cross-module acceptance. No open PR in the table above produces them.

1. **100-peer independent-host soak** (T04, #176, #255). Distinct host fingerprints, `measurement_class=operator_attested_real_multi_host`, node binary SHA-256, latency, loss, and outage recovery. The Tailscale stress run is a different test.
2. **2/3-host E2EE with node binary hashes** (T03, #176, #177). Re-dispatch on the release candidate. Payload SHA-256 in the 2026-11-07 summary is not a binary hash. Three-host run 42430583187 succeeded at `bd56475` without that hash. Two-host run 42430580795 failed at the same commit. Neither run is `5176eae`.
3. **Broker OMS reconciliation** (T14, #179). Local synthetic ledger tests are not a broker sandbox, an OMS state machine, or a broker-state match.
4. **Independent cryptography and risk review** (T06, T13, T20, #177, #179, #182). A self-checked inventory is not that review.
5. **Feed licence signoff** (T09, #178). Closed issue #178 and open PR #264 do not record redistribution rights.

## Ordered next steps

1. **#176 / #255.** Run the post-throttle 100-peer soak on hosts with distinct fingerprints and retain the artifact. Do not cite run 42495167275 as that soak.
2. **#176 / #177.** Re-dispatch the two-host and three-host E2EE workflows on the candidate and store each node binary SHA-256 with the run URL.
3. **#171 / #181.** Decide the paste maintenance finding, point the advisory report source commit at the candidate (lock hashes already match `5176eae`), and teach `generate_dependency_sbom.py` to ingest `requirements-lock.txt`. Add `check_license_provenance.py` to Dependency Security Audit if T01 is to move.
4. **#178.** Attach a feed-licence signoff before anyone ticks T09. Review #264 and #273 as scaffolding.
5. **#179.** Review #268, then #265. Add kill-switch and stale-feed checks to the paper workflow path and run them in CI before T16 can move. Broker reconciliation is separate work.
6. **#180.** Review #271 against `admin_centre`. Capture one dashboard session fed by a running p2p-node before T17 can move.
7. **#181.** Review #272. Retain a backup and restore drill before T18 can move.
8. **#182.** Keep #274 on hold until the binder uses the soak measurement class. Leave `python scripts/check_release_gate_status.py --strict` unused until the five gaps above have artifacts.
9. **#207.** Tick a checkbox only after its row in this table has a merged commit and a passing run or artifact that meets the checkbox text.

The scenario list for #182 is the status table above. A separate end-to-end checklist file is a follow-up; older matrix docs are not this inventory.

## Discrepancies

1. **Mutable action tags are not present on this commit.** A scan hint said #167 was closed while 59 tag-based refs were still release-blocking. Enumeration of `.github/workflows/*.yml` at `5176eae` found 9 files, 68 `uses`, 0 mutable tags. The ratchet runs in CI 42494850551. #167 was closed `COMPLETED` at 2026-11-08T06:47:21Z, and PR #240 merged later at 2026-11-08T11:26:12Z (`6c6d155`). `docs/workstreams/ISSUE-167.md` still says the workstream branch was only initialized. Trust review of the pinned SHAs is still open, and that is T06, not T02.
2. **#207 is 0/20 ticked while T02 has CI evidence and twelve other rows are Partial.** The kickoff comment correctly says the checklist is not completion. The unchecked pinning box is the row that now lags the code.
3. **#178 is closed. Feed licences are not verified.** It closed at 2026-11-08T09:24:12Z with PR #243. `authorized_feeds.py` states that no licence is claimed. #264 and #273 are still open and unreviewed. T09 stays Remaining.
4. **#171 was not rewritten after PR #239.** The issue body still lists hickory 0.25.2, yamux 0.12.1, and an unresolved Python environment at commit `212cad6`. The 08:39Z comment still says the Python lock is unresolved. PR #239 merged at 15:21Z. The current advisory file, queried 11:20:57Z at `587dbc71`, reports PyPI scanned (24 packages) and 0 vulnerability groups, and its input hashes match the worktree at `5176eae`. paste remains. The SBOM generator still says Python resolution is unresolved. The issue stays correctly open; its text is stale.
5. **#180 is open after implementation evidence landed.** PR #245 merged and Quant API CI 42471703916 passed. The issue comment records RBAC, redaction, and web tests, and it also says peer telemetry needs an external p2p-node. That is Partial, not a completed dashboard acceptance. #271 is additional scaffolding.
6. **WIP-titled PRs merged to main:** #240, #241, #242, #243, #244, #245, #246, #247, #248, #266, and #267. A WIP title did not stop the merge. Several `docs/workstreams/ISSUE-*.md` files still say the branch was only initialized. Title and stub docs are not acceptance status; the table above is.
7. **Tailscale can be green while independent-host soak claims stay false.** Run 42495167275 really did exchange observations between two GitHub runners. Checked-in soak JSON sets `independent_hosts_verified` to false. #269 tightens a classifier that main already tests in `scripts/test_run_p2p_soak.py`. Both facts stand. The green Tailscale run does not satisfy T03 or T04.
8. **#264 and #265 check acceptance boxes their diffs do not meet.** Both bodies say the PR is planning only. #264's streaming gate does not dial a vendor. #265's paper gate does not place or reconcile broker orders.
9. **Quant API path-filtered CI did not rerun on the final main push.** The newest Quant API CI success on main is [run 42494399120](https://github.com/Osiris6900/osiris-eye/actions/runs/42494399120) at `e2d49a8` (PR #239). Commit `5176eae` is covered for the same pytest suite by the main CI python job in run 42494850551, because that workflow is not path-filtered.
10. **#255 stays open after the throttle merged.** `services/p2p-node/src/rejection_log.rs` and PR #256 / #259 are on main with green rust CI. The issue comment correctly keeps it open until a post-throttle 100-peer multi-host soak exists. That soak is T04, not a separate #207 checkbox. #279 adjusts the throttle lines and is not that soak.
11. **Correction to the earlier T03 wording.** Three-host run [42430583187](https://github.com/Osiris6900/osiris-eye/actions/runs/42430583187) succeeded at `bd56475`. The failure at that commit is two-host run [42430580795](https://github.com/Osiris6900/osiris-eye/actions/runs/42430580795). Neither run retains a node binary hash, and neither is `5176eae`, so T03 stays Remaining.

## Release sequence

Gates stay sequential. Parallel module work does not promote a later gate.

| Gate | Owner | Status on this baseline | Blocker |
| --- | --- | --- | --- |
| G0 Reproducible build | #167, #171, #181 | Partial | T02 is evidenced. T01 is Partial. |
| G1 P2P acceptance | #176, #177, #255 | Remaining | T03, T04, T05. |
| G2 Data acceptance | #178 | Remaining | T09 licence signoff. T07, T08, and T10 are Partial. |
| G3 Research acceptance | #178 | Partial | T11 and T12 have unit tests and no candidate report. |
| G4 Paper acceptance | #179 | Partial | T13–T16. Broker OMS reconciliation is outstanding. |
| G5 Operational review | #180, #181, #182 | Remaining | T06, T18, and T20. T17 and T19 are Partial. |
| G6 Live pilot | #207 | Remaining | Separate operator authorization after G0–G5. |

## Module notes

- **#171.** Vulnerability matches for the old Hickory and Yamux versions are gone from the locked graph. Python pins and a PyPI OSV scan exist. Provenance is not closed. See T01.
- **#179.** Risk limits, a durable synthetic ledger, and admin kill-switch routes are merged and tested. The paper workflow path and any broker OMS are not accepted. See T13–T16.
- **#180.** The admin centre from PR #245 is the implementation evidence. It is process-local. See T17.
- **#255.** Rejection-log throttling is merged. The soak that motivated it has not been repeated on independent hosts. See T04.
- **#176, #177, #181, #182.** Local process, relay, revocation, and receipt tests run in the rust job of CI 42494850551. That is local acceptance, not the multi-host evidence named above.
