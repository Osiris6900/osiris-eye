# Gate merge order and next evidence

Base of this note: `main`. The checkbox inventory is [PR #275](https://github.com/Osiris6900/osiris-eye/pull/275). Refs #207 and #182. Live trading is not authorized. Merging a row below does not by itself satisfy a tracker checkbox.

## Merge order

**#302** (#171, T01) has merged and is dropped from the pending order. Merge `8ef21a84846a0e525978d6589b458caf3afc75e4`, head `d47d14d0c9b429fbc5c6a7d9c8df6fc75bec961c`. [CI 42677260186](https://github.com/Osiris6900/osiris-eye/actions/runs/42677260186) and [Dependency Security Audit 42677259943](https://github.com/Osiris6900/osiris-eye/actions/runs/42677259943) passed on that head, including rust-audit. It sets the `libsqlite3-sys >= 0.39.0` ratchet and refreshed `docs/P2P_DEPENDENCY_ADVISORIES.json` at source `2501a07441c7a3e124a7494f2384903c85f5b6ca`. Later refreshes replaced that file; the current tree source is `2b685c2bc8689422724f1d9eb52ceef660ebb8a7`, which still matches `facts.advisory_source_commit`. `facts.cargo_versions` still match. rusqlite 0.41.1 and libsqlite3-sys 0.39.1 were already on main's parent `d42e422edaa928e907f519de055a766e8e7e14ce` when #302 merged. T01 stays Partial.

**#306** (#171, T01) has also merged, at `26fd658f9f49463fca36e8a0eedc0344881b046a`. Its lock change is the same rusqlite 0.41.1 / libsqlite3-sys 0.39.1 fix and is superseded by #302 and by the lock already on main. Supersede rather than merge another copy. The only unique delta versus #302 is `fetch-depth: 0` in `.github/workflows/release-gate-inventory.yml`, and that is already on main. [Audit 42679453468](https://github.com/Osiris6900/osiris-eye/actions/runs/42679453468) passed. [CI 42679453672](https://github.com/Osiris6900/osiris-eye/actions/runs/42679453672) was cancelled. There is no lock conflict to resolve.

1. **#307** (#181, #182; T05, T18, T19) follows the lock merge and has merged at `187f0c16a2afdb3a37bbe3950d841816d8fe5115` (head `efc40cd1040d33042586e190e834f33cdd1237cb`). The branch was rebuilt onto main after the lock merge, so it does not duplicate the rusqlite change. [CI 42685745608](https://github.com/Osiris6900/osiris-eye/actions/runs/42685745608), [dependency audit 42685745207](https://github.com/Osiris6900/osiris-eye/actions/runs/42685745207), and [dry-run 42685744834](https://github.com/Osiris6900/osiris-eye/actions/runs/42685744834) passed on that merge commit. The dry-run only checks a plan with `drill_status: not_run`. It does not implement T05, T18, or T19. Next evidence: a real staging recovery drill artifact that conforms to `docs/evidence/deploy-recovery.schema.json`.
2. **#276** (#171) has merged at `abf3f0439fd2`. Hickory/Yamux lock floor. T01 stays Partial. **#281** has merged at `223c83d8ae23` and only records the #171 evidence boundary. **#286** has merged at `b600a6516726` and teaches the SBOM to read the hashed Python lock. It does not clear paste.
3. **#279** (#255, #176) has merged at `734ee5abc9e75ce4d61d47b8d4e97c93e3664bd8` (head `b75304691a6baee76580d1f8e5d94b892cf655d9`). Rejection-log throttle lines. No retarget remains. T04 stays Partial.
4. **#280** (#176, #255) has merged at `d42e422edaa928e907f519de055a766e8e7e14ce` (head `b47ee6ce4bf4ae836b952356680366b838e14893`). Local reconnect-tick backoff only. [CI 42677901050](https://github.com/Osiris6900/osiris-eye/actions/runs/42677901050) and [Dependency Security Audit 42677901276](https://github.com/Osiris6900/osiris-eye/actions/runs/42677901276) passed on that merge commit. This is not multi-host evidence. #176 and #255 stay open. T04 stays Partial. **#283** has merged after it (head `95ca079cd7441ba2a00dca3c734fdc838f51572d`).
5. **#291** (#176) has merged at `5868fb45fe6b3d85651a74caab4e4f81cf6233a7`.
6. **#310** (#177, T03, T06) follows #280, #283, and #291 and has merged at `a49b5ca6318088d6f7b885c5bf568999e0e3788f` (opened head `b74a8247f3f7894cb70f26bbeaa6b9d813bf53d7`, pre-merge head `c91b2c1c7d633229914234502925010e93bdd341`). Head CI is green: [CI 42680025127](https://github.com/Osiris6900/osiris-eye/actions/runs/42680025127) and [audit 42680025148](https://github.com/Osiris6900/osiris-eye/actions/runs/42680025148) on `c91b2c1`; opened-head [CI 42678563362](https://github.com/Osiris6900/osiris-eye/actions/runs/42678563362) and [audit 42678563348](https://github.com/Osiris6900/osiris-eye/actions/runs/42678563348) also passed. Merge-push [CI 42704043885](https://github.com/Osiris6900/osiris-eye/actions/runs/42704043885) was cancelled; [audit 42704043998](https://github.com/Osiris6900/osiris-eye/actions/runs/42704043998) passed. Scaffolding only. T03 and T06 stay unchanged. Runs 42424830531, 42424833409, and 42430583187 predate current main. Later main commits do have successful recipient-only runs (three-host 42724204891 at `8a531b37`, two-host 42717480948 at `edae0ba3`). Current tip `9f9e27a` has no recipient-only run.
7. **#277** (#179) has merged. Kill switch, stale data, and an internal paper reconcile. No broker order.
8. **#305** (#179, T14) follows #277. It merged as scaffolding at `deed94692cd92096d84d8657e3d12034691d8466` (opened at `fe90bae1778b7e30a17a5e991c9a585a71718b60`, head `cb83b019ab5a897938623d052458c6b6d6f706a5`). Quant API CI 42683473725 and dependency audit 42683473791 passed; main CI 42683473981 was cancelled. T14 stays Partial: no broker-sandbox match. The daily-loss gate includes adverse unrealized loss and fails closed when another holding has no fresh mark. Live execution stays disabled. The merge touches `paper_execution.py` (894 insertions) and a new lifecycle test. It does not edit `durable_paper_ledger.py`. Both paper modules remain on main as separate books, so this is placed after #277 rather than held. Main CI [42683473981](https://github.com/Osiris6900/osiris-eye/actions/runs/42683473981) on `deed94692cd92096d84d8657e3d12034691d8466` was cancelled and could not be re-run. [CI 42748111800](https://github.com/Osiris6900/osiris-eye/actions/runs/42748111800) is the substitute run: it passed on `3a126f9a04e384229ade0313675c20ab90b44b49`, where `paper_execution.py` is unchanged since #305's merge.
9. **#439** (#179, T14) has merged at `71b3d4de0a85edd28bd1a6ec0f5457af58c14558` (head `906d53942a8ce682ecb6e7d0267b41016709a289`). Evidence class is scaffolding. Head [CI 43493100907](https://github.com/Osiris6900/osiris-eye/actions/runs/43493100907) passed python, rust, and web. [Dependency Security Audit 43493101800](https://github.com/Osiris6900/osiris-eye/actions/runs/43493101800) passed, including rust-audit. [Quant API CI 43493102241](https://github.com/Osiris6900/osiris-eye/actions/runs/43493102241) and [179 evidence contract 43493100925](https://github.com/Osiris6900/osiris-eye/actions/runs/43493100925) passed. Merge [CI 43494481576](https://github.com/Osiris6900/osiris-eye/actions/runs/43494481576) passed python, rust, and web. The daily-loss gate now includes adverse unrealized loss. T14 stays Partial because there is no broker-sandbox match.
10. **#442** (#179, T14) has merged at `78818b92618e9e230f4a9fa4c7e42fe3be9fd553` after #439 (head `f7ca859c576e2d5403e6c1c5f569aea6e01a5733`). Evidence class is scaffolding. A buy projects held and new quantity at a fresh higher mark. Head [CI 43501436283](https://github.com/Osiris6900/osiris-eye/actions/runs/43501436283) passed python, rust, and web. [Audit 43501436218](https://github.com/Osiris6900/osiris-eye/actions/runs/43501436218) passed rust-audit. [Quant API CI 43501436401](https://github.com/Osiris6900/osiris-eye/actions/runs/43501436401) passed. T14 stays Partial because there is no broker-sandbox match. Live execution stays disabled. Two local failures on unmodified main are environmental and are not product bugs: `test_control_plane_coordination.py::test_review_and_actual_restricted_worker` fails when `osiris_api` is not installed on the isolated interpreter (`python -I`); it passes after that install. `test_verified_receipt_rust_bridge.py::test_rust_verifier_persists_to_python_ledger` fails on Cargo 1.83 because `edition2024` is required. Head CI uses a newer toolchain and passed both.
11. **#309** (#178, #179; T08, T16) has merged at `2f616c2258eafa220b34895ae5d123a4d7ff1907` (opened head `bd85dac05289d529b0013be6561a7771ebd1743d`, final head `7d0599fea4a20b9c454ef437a1dc44c7b01f981e`). Scaffolding only. T08 and T16 stay Partial. Opened-head rust-audit [42676883926](https://github.com/Osiris6900/osiris-eye/actions/runs/42676883926) failed on RUSTSEC-2026-0459. The follow-up head is green: [CI 42679533894](https://github.com/Osiris6900/osiris-eye/actions/runs/42679533894) and [audit 42679534042](https://github.com/Osiris6900/osiris-eye/actions/runs/42679534042). Merge [CI 42684177898](https://github.com/Osiris6900/osiris-eye/actions/runs/42684177898) and [audit 42684177853](https://github.com/Osiris6900/osiris-eye/actions/runs/42684177853) passed. The rust job on the opened head succeeded, so a local Cargo 1.83 edition2024 parse error did not fail CI. It is no longer waiting for CI.
12. **#268** (#179) has merged at `2428932a9abf`. Paper risk boundary tests. T13 and T16 stay Partial.
13. **#278** (#180) has merged. Read-only fills and outbox status; `peer_count` stays null.
14. **#284** (#180) has merged after #278. The read-only enrolled-identity registry passed CI and dependency audit on its PR head. This is enrollment inventory, not live peer telemetry; T17 stays Partial.
15. **#308** (#180) is closed without a merge. Stacked on #284; scaffolding only; tracker items none. Head `3975e8561318a8bb9c360b01aff2d0c2b941861e` has green CI [42679237031](https://github.com/Osiris6900/osiris-eye/actions/runs/42679237031), audit [42679237305](https://github.com/Osiris6900/osiris-eye/actions/runs/42679237305), and Quant API CI [42679237321](https://github.com/Osiris6900/osiris-eye/actions/runs/42679237321). T17 and T18 stay Partial. It conflicts with main and is not reopened here.
16. **#271** (#180) has merged at `0dfdfa4fa8dd`. Authenticated health projection. T17 stays Partial.
17. **#438** (#180, T17) has merged at `b57ff6bea6795cdb7d374721e2b99eec4a30862a` after #278, #284, and #271. Head `22c829c4795668fbcac486283dcbe2e5c041cc2b` is green: [CI 43486289288](https://github.com/Osiris6900/osiris-eye/actions/runs/43486289288), [Quant API CI 43486289127](https://github.com/Osiris6900/osiris-eye/actions/runs/43486289127), [audit 43486289381](https://github.com/Osiris6900/osiris-eye/actions/runs/43486289381), [179 evidence contract 43486289393](https://github.com/Osiris6900/osiris-eye/actions/runs/43486289393), and [182 evidence contract 43486289124](https://github.com/Osiris6900/osiris-eye/actions/runs/43486289124). Evidence class is scaffolding. The pull request says this is not accepted operations evidence. T17 stays Partial.
18. **#440** (#180, T17) has merged at `35fd514d06f7d897b1aa68be792f3b803abd69dc` after #438. Head `b4a4130c530f2ddb2c0d42c74271553b73f970c0` is green: [CI 43495262061](https://github.com/Osiris6900/osiris-eye/actions/runs/43495262061) passed python, rust, and web; [audit 43495262176](https://github.com/Osiris6900/osiris-eye/actions/runs/43495262176) passed rust-audit; [Quant API CI 43495261981](https://github.com/Osiris6900/osiris-eye/actions/runs/43495261981), [179 contract 43495262506](https://github.com/Osiris6900/osiris-eye/actions/runs/43495262506), and [182 contract 43495262035](https://github.com/Osiris6900/osiris-eye/actions/runs/43495262035) passed. Evidence class is scaffolding. The pull request says this does not move T17 to accepted operations evidence. T17 stays Partial.
19. **#272** (#181) has merged at `5c76c17aa1db`. Staging configuration check. Not a restore drill. T18 stays Partial.
20. **#270** (#177) has merged at `0fc558af61b3`. Receipt replay and revocation gate. T06 stays Remaining.
21. **#273** (#178) has merged at `1fa06452df8f`. Out-of-sample eligibility. T12 stays Partial.
22. **#264** (#178) has merged at `2fa400092d10`. Offline feed validator. T09 stays Remaining.
23. **#274** (#182) has merged at `990f6b6f9ac0`. Candidate and host evidence binding. It does not accept T04 or T19.
24. **#265** (#179) has merged at `2036888bbada`. Read beside `durable_paper_ledger.py` and #277. T14 stays Partial. Live execution stays disabled.
25. **#269** (#176, #255) has merged at `3865acce897d`. It blocks false independent-host soak claims. T04 stays Partial.

## Merged 2026-11-10 through 2026-11-13

No pull request in this window is closed without a merge. None of these merges moves a row to Implemented with evidence. The remaining blockers are the ones in `docs/workstreams/NON_BROKER_EXECUTION_2026_11_13.md`.

| PR | Merged | Touches | Gate effect |
| --- | --- | --- | --- |
| #321 | 2026-11-10 | T19 | Dispatchable capacity workflow. Not sustained independent-host acceptance. |
| #320 | 2026-11-10 | T18 | Dispatchable recovery dry-run. Not a passed staging drill. |
| #319 | 2026-11-10 | inventory | Baseline and ghost-mode evidence note. |
| #324 | 2026-11-10 | T04, T19 | Capacity receiver timeout fix. |
| #325 | 2026-11-10 | T20 | Pending-only paper signoff worksheet. T20 stays Remaining. |
| #326 | 2026-11-10 | T16 | Fail closed when paper workflow controls are absent. |
| #327 | 2026-11-10 | T18 | Isolated paper-ledger backup and restore. |
| #328 | 2026-11-10 | T04, T19 | 10/50/100 report verification and host commitments. |
| #329 | 2026-11-10 | T16 | Bind paper-order forwarding to controlled intent. |
| #330 | 2026-11-10 | T07, T09 | Bind feed ticks to reviewed entitlement. T09 stays Remaining. |
| #331 | 2026-11-10 | T11, T12 | Synthetic-only walk-forward archive. |
| #332 | 2026-11-10 | T07, T09 | Persist feed suspensions. T09 stays Remaining. |
| #333 | 2026-11-10 | T04, T19 | Record the limited 3-host capacity run. |
| #334 | 2026-11-10 | T16 | Isolated paper-ledger failure injection. |
| #335 | 2026-11-10 | T19 | Bind receipt evidence to hashed payloads. |
| #336 | 2026-11-10 | T08 | Decision-time freshness for authorized ticks. |
| #337 | 2026-11-10 | T04 | Retry a Tailscale endpoint artifact 404. |
| #338 | 2026-11-10 | T06 | Crypto-audit commissioning scope. T06 stays Remaining. |
| #339 | 2026-11-10 | T18 | Recovery evidence URL syntax. |
| #340 | 2026-11-10 | T18 | Local paper DR artifact checks. |
| #341 | 2026-11-10 | T18 | Reject unexpected recovery artifacts. |
| #342 | 2026-11-11 | T06 | Reviewer intake checklist. T06 stays Remaining. |
| #343 | 2026-11-11 | T18, T19 | Operator evidence runbook. |
| #344 | 2026-11-11 | T08, T14, T15 | Market data into durable paper execution. Broker acceptance stays deferred. |
| #345 | 2026-11-11 | T15 | Deterministic adverse paper slippage. |
| #346 | 2026-11-11 | T17 | Local P2P health in Admin Centre. |
| #347 | 2026-11-11 | T18 | Portable paper backup across CI jobs. |
| #349 | 2026-11-11 | T16 | Child-process crash and exactly-once paper recovery. |
| #350 | 2026-11-11 | T04 | Opt-in 15-minute 100-peer capacity mode. Not independent-host acceptance. |
| #351 | 2026-11-12 | T18 | Fail-closed cross-runner paper DR verification. |
| #352 | 2026-11-12 | T06 | Cryptography review intake checks. T06 stays Remaining. |
| #353 | 2026-11-12 | T09 | Fail-closed vendor feed-rights intake. T09 stays Remaining. |
| #354–#362 | 2026-11-11 | P2P prototypes | Ghost trade consistency, fail-closed live gate, routing, network intelligence, and audit chain. Live trading stays unauthorized. |
| #372 | 2026-11-11 | integration | Nine workstreams with a fail-closed evidence gate. |
| #385 | 2026-11-11 | T08 | Market quality and clock-skew guards. |
| #401 | 2026-11-11 | T13 | Portfolio stress and signal-conflict advisors. |
| #402 | 2026-11-11 | T11 | Experiment registry and lineage. |
| #403 | 2026-11-11 | T11, T12 | Model drift and shadow advisory. |
| #404 | 2026-11-11 | T19 | Unit tests for new platform modules. |
| #406 | 2026-11-11 | T04 | Outage detection from bounded health history. |
| #407 | 2026-11-12 | T17 | Live-trading setup draft. Live execution stays disabled. |
| #408 | 2026-11-12 | T14–T19 | Governed paper platform, receipts, dashboard, and evidence hardening. |
| #410 | 2026-11-11 | T04 | Connectivity-loss and provenance regression gates. |
| #411 | 2026-11-11 | T14, T15 | Cash, position, and fee reconciliation invariants. |
| #417 | 2026-11-12 | T08 | Reject ambiguous feed trust and stale event timestamps. |
| #418 | 2026-11-12 | T14 | Bind broker acknowledgements to order intent. No external broker acceptance. |
| #419 | 2026-11-12 | T17 | Admin Centre uses the canonical execution account. |
| #420 | 2026-11-12 | T14, T17 | Reconcile paper projections with the decision and fill journal. |
| #421 | 2026-11-12 | T11 | Immutable experiment records and backtest exports. |
| #422 | 2026-11-12 | T16, T18 | Close authorization, telemetry, and recovery gaps found in the workstream audit. |
| #424 | 2026-11-12 | T17 | Canonical account, risk, and market history views. |
| #425 | 2026-11-12 | integration | Govern source views and verify W01–W20 together. |
| #426 | 2026-11-12 | T14 | Bound decimal expansion and preserve state on refused live requests. |
| #427 | 2026-11-12 | T14, T18 | Prevent lease revival and verify broker fills and custody bytes. |
| #428 | 2026-11-12 | T16, T19 | Trace verified Rust receipts through risk, fills, and process recovery. |
| #429 | 2026-11-12 | T01 | Include native and risk locks in retained SBOM evidence. paste remains. |
| #430 | 2026-11-12 | T08 | Atomic L2 history, clock rollback, and depth estimates. |
| #432 | 2026-11-12 | T01 | Verify the retained SBOM against the committed dependency graph. |
| #433 | 2026-11-13 | T13 | Capped portfolio risk preview. |
| #434 | 2026-11-13 | T01–T20 | Receipt-paper evidence and the seventeen-gate checkpoint. No gate accepted. |
| #435 | 2026-11-13 | T14 | Ghost/keyless default and optional paper-broker settings. External broker acceptance stays deferred. |
| #436 | 2026-11-13 | T01, T19, #182 | Merged at `9f9e27a3cf286cb3bb95cf33b5a245ec2b413118`, head `666a3e5599363f6347e671664b8135aeab0d38e6`. All seven head workflows passed: [CI 43416302724](https://github.com/Osiris6900/osiris-eye/actions/runs/43416302724), [platform tests 43416302829](https://github.com/Osiris6900/osiris-eye/actions/runs/43416302829), [182 contract 43416302491](https://github.com/Osiris6900/osiris-eye/actions/runs/43416302491), [pin ratchet 43416302626](https://github.com/Osiris6900/osiris-eye/actions/runs/43416302626), [179 contract 43416302762](https://github.com/Osiris6900/osiris-eye/actions/runs/43416302762), [inventory 43416302532](https://github.com/Osiris6900/osiris-eye/actions/runs/43416302532), [audit 43416302544](https://github.com/Osiris6900/osiris-eye/actions/runs/43416302544). Counts stay 3 evidenced / 14 partial / 3 remaining. |

## Next evidence

| Step | Owner | Done when |
| --- | --- | --- |
| Staging recovery drill | #181, #182 (T05, T18, T19) | A real staging deploy and rollback artifact that conforms to `docs/evidence/deploy-recovery.schema.json`, retained at `docs/evidence/captured/deploy-recovery.json`, with `recovered_healthy` true and `live_trading_authorized` false. #307's dry-run is not that artifact. |
| 100-peer independent-host soak | #176, #255 (T04) | Distinct host fingerprints, `measurement_class=operator_attested_real_multi_host`, node binary SHA-256, latency, loss, and outage recovery on the candidate commit. |
| 2/3-host E2EE with binary hashes | #176, #177 (T03) | Two-host and three-host workflows re-run on the candidate, each node binary SHA-256 retained with the run URL. |
| OMS reconciliation | #179 (T14) | Broker sandbox state matches paper orders and fills after retry and restart. #305's paper lifecycle is scaffolding on the internal book and does not match a broker sandbox. The daily-loss gate includes adverse unrealized loss and fails closed when another holding has no fresh mark. |
| Independent crypto and risk review | #177, #179, #182 (T06, T13, T20) | Named reviewer, scope, commit, and an approved decision. This checklist is not that review. |
| Feed licence signoff | #178 (T09) | Written entitlement and redistribution decision for each admitted vendor. #264 does not provide it. |
