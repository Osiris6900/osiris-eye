# Integrated Delivery Gates — verified inventory 2026-11-08

## 2026-11-13 non-broker verification hardening

Ghost/keyless remains the default; actual external broker acceptance is deferred.
The retained OSV scan now covers all three Rust roots, including native-feed's
previously omitted `memmap2`, and consumes bounded continuation pages. Its 608
exact packages yielded zero vulnerability groups and the existing `paste`
maintenance warning. Offline CI verifies all six input hashes against the
recorded source commit. The 29-check engineering profile also enforces Python
lock/licence policy and rejects missing, empty or skipped backend JUnit evidence.
See [implementation, commands and per-gate remaining work](workstreams/NON_BROKER_EXECUTION_2026_11_13.md).
[#436](https://github.com/Osiris6900/osiris-eye/pull/436) merged at `9f9e27a3cf286cb3bb95cf33b5a245ec2b413118` (head `666a3e5599363f6347e671664b8135aeab0d38e6`). All seven head workflows passed: [CI 43416302724](https://github.com/Osiris6900/osiris-eye/actions/runs/43416302724), [platform module tests 43416302829](https://github.com/Osiris6900/osiris-eye/actions/runs/43416302829), [182 evidence contract 43416302491](https://github.com/Osiris6900/osiris-eye/actions/runs/43416302491), [action pin ratchet 43416302626](https://github.com/Osiris6900/osiris-eye/actions/runs/43416302626), [179 evidence contract 43416302762](https://github.com/Osiris6900/osiris-eye/actions/runs/43416302762), [release-gate inventory 43416302532](https://github.com/Osiris6900/osiris-eye/actions/runs/43416302532), and [dependency audit 43416302544](https://github.com/Osiris6900/osiris-eye/actions/runs/43416302544). This is supporting evidence for T01 and T19. It does not accept any of the 17 open gates.
Counts remain **3 evidenced / 14 partial / 3 remaining**.

## 2026-11-13 exact-main verification checkpoint

At main `43157b1bad5a93c8bc441a21a04b8572a9e736f5`, five observed workflows /
eleven jobs passed. Three actual downloaded ZIPs match GitHub's digests: the
613-component SBOM reproduces from locked sources; the receipt report matches
all 799 regular source files and its two risk/fill/recovery traces; the market
report's eight declared source hashes, sidecar and restart accounting match.
See [verification and limits](release/VERIFICATION_2026_11_13.md) and
[machine-readable evidence](release/VERIFIED_ARTIFACTS_2026_11_13.json).
The receipt verifier now repeats source-completeness and trace-consistency
checks in CI before upload and in the 26-check engineering profile. Short P2P
success is not sustained capacity/DR acceptance. **0 of the seventeen open
gates gained acceptance**; counts remain **3 evidenced / 14 partial / 3 remaining**.
The T01/T13–T16/T19 evidence references in the JSON are updated to this checkpoint;
their blocker and acceptance status are unchanged.

## 2026-11-12 downloaded-byte verification checkpoint

At main `5b43e61c8c974ef4e52b22c32dbd9cba17ccc62c`, all three core CI jobs,
all four dependency-security jobs and the synthetic research archive job passed.
Five actual downloaded artifacts were checked against their ZIP/member hashes;
the complete 613-component SBOM was reproduced and the receipt report's 792
source-file digests were compared with that commit. Exact runs, digests, expiry
and limits are in [the verification record](release/VERIFICATION_2026_11_12.md)
and [its JSON inventory](release/VERIFIED_ARTIFACTS_2026_11_12.json).
The SBOM verifier now repeats the locked-source comparison in CI before upload.
Stale open-PR references in the current table and JSON were cleared after reading
the actual open-PR collection; the archived PR tables below remain historical.
Gate statuses remain **3 evidenced / 14 partial / 3 remaining**, with no new
independent acceptance, paper release signoff or live-trading authorization.

## 2026-11-12 current execution of the seventeen open gates

The candidate on top of #427 adds actual Rust-receipt to canonical paper-order
execution and process-loss recovery. Current implementation, local check coverage
and the exact remaining requirement for every open gate are documented in
[RELEASE_GATE_EXECUTION_2026_11_12.md](workstreams/RELEASE_GATE_EXECUTION_2026_11_12.md).
The engineering profile maps 25 checks, including complete offline SBOM generation, to both W01–W20 and T01–T20.

The current blocker column below distinguishes implemented adapters, source views,
model archives, paper reconciliation, actual-node telemetry and temporary
backup/restore from missing independent acceptance. Its historical evidence
column is not a claim that old runs validate the new candidate. T20 remains a
paper-only signoff; no live pilot is authorized. Acceptance counts and statuses
remain 3 evidenced / 14 partial / 3 remaining.

## 2026-11-11 integrated research/operations candidate

The twenty implementation areas are mapped in [PLATFORM20_IMPLEMENTATION.md](PLATFORM20_IMPLEMENTATION.md). They are locally tested MVP services, not twenty accepted release gates. Existing gate statuses remain 3 evidenced, 14 partial and 3 remaining. The candidate adds native-feed and browser integration checks; immutable action counts are recorded by `docs/release-gate-status.json`, rather than inferred from historical snapshots below. The optional infrastructure SDK and external cloud/vendor configurations still require their own deployment, provenance and acceptance review.

## 2026-11-11 CI coverage update

The platform-module workflow covers the six existing P2P/evidence modules and, once present, the eight new data, risk, model, history and review-intake modules. A module without its test file or with zero discovered tests fails the job. Only modules whose implementation and tests are both absent may be skipped as not yet merged. Adding this workflow changes the inventory to 19 workflow files and 113 pinned action uses, with zero mutable action uses. Acceptance statuses remain 3 implemented with evidence, 14 partial and 3 remaining; unit-test coverage is not independent release acceptance.

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

| Tracker item | Module issue | Status | Evidence | — | Missing evidence / blocker |
| --- | --- | --- | --- | --- | --- |
| T01. Remediate OSV dependencies (#171) and resolve Python lock/provenance gate | #171 | Partial | [PR #239](https://github.com/Osiris6900/osiris-eye/pull/239) and [PR #257](https://github.com/Osiris6900/osiris-eye/pull/257) merged. [Audit 42494850772](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850772) green at `5176eae`. [`docs/P2P_DEPENDENCY_ADVISORIES.json`](P2P_DEPENDENCY_ADVISORIES.json) queried 2026-11-13T11:15:07Z against dependency inputs at `2b685c2bc8689422724f1d9eb52ceef660ebb8a7`: all three Rust roots, npm and Python, 608 exact packages, 0 vulnerability groups and 1 maintenance-only group; all six input hashes match. Hickory 0.26.3 and yamux 0.14.1 are the only copies in `services/p2p-node/Cargo.lock`. Hashed lock: `services/quant-api/requirements-lock.txt`. [#302](https://github.com/Osiris6900/osiris-eye/pull/302) merged at `8ef21a84846a0e525978d6589b458caf3afc75e4` (head `d47d14d0c9b429fbc5c6a7d9c8df6fc75bec961c`): rusqlite 0.41.1, libsqlite3-sys 0.39.1, ratchet floor `libsqlite3-sys >= 0.39.0`, and [`docs/P2P_DEPENDENCY_ADVISORIES.json`](P2P_DEPENDENCY_ADVISORIES.json). [CI 42677260186](https://github.com/Osiris6900/osiris-eye/actions/runs/42677260186) and [audit 42677259943](https://github.com/Osiris6900/osiris-eye/actions/runs/42677259943) passed on that head. [#306](https://github.com/Osiris6900/osiris-eye/pull/306) merged at `26fd658f9f49463fca36e8a0eedc0344881b046a`; its lock change is the same fix and is superseded. [Audit 42679453468](https://github.com/Osiris6900/osiris-eye/actions/runs/42679453468) passed. [CI 42679453672](https://github.com/Osiris6900/osiris-eye/actions/runs/42679453672) was cancelled. | — | T01 stays Partial. RUSTSEC-2026-0459 is cleared. paste 1.0.15 (RUSTSEC-2024-0436) remains. The SBOM generator no longer marks Python resolution unresolved, and Dependency Security Audit already runs `scripts/check_license_provenance.py`. Accepted final-candidate audit, SBOM, and provenance artifacts are still required. `facts.cargo_versions` and `advisory_source_commit` still match the worktree (`2b685c2bc8689422724f1d9eb52ceef660ebb8a7`). #302 recorded source `2501a07441c7a3e124a7494f2384903c85f5b6ca`; later refreshes replaced that file. Do not merge another copy of the lock. #436 merged at `9f9e27a3cf286cb3bb95cf33b5a245ec2b413118`; head audit [43416302544](https://github.com/Osiris6900/osiris-eye/actions/runs/43416302544) passed. The scanner covers all locked roots. Final-candidate audit, SBOM, and provenance acceptance, and a reviewed paste disposition, remain open per `docs/workstreams/NON_BROKER_EXECUTION_2026_11_13.md`. |
| T02. Implement immutable action pinning and CI ratchet (#167) | #167 | Implemented with evidence | [PR #240](https://github.com/Osiris6900/osiris-eye/pull/240) merged as `6c6d155110c796d43bf5c879ea1487e1620ac81a`. [CI 42494850551](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850551) python job runs `scripts/check_action_pinning.py` and succeeded at `5176eae`. Nine workflow files contain 68 `uses` references, all 40-character SHAs, zero mutable tags. | — | Checkbox evidence is the SHA ratchet on CI. A separate trust review of those pinned commits remains on T06. |
| T03. Re-run final-candidate 2/3 host E2EE with binary hashes and retained evidence | #176, #177 | Implemented with evidence | Verified [three-host run 42724204891](https://github.com/Osiris6900/osiris-eye/actions/runs/42724204891) with retained `three-e2ee-evidence-42724204891-1` artifact (14 days); [two-host run 42720301712](https://github.com/Osiris6900/osiris-eye/actions/runs/42720301712) succeeded. Binary-hash artifact contents require separate review. [#310](https://github.com/Osiris6900/osiris-eye/pull/310) merged as scaffolding at `a49b5ca6318088d6f7b885c5bf568999e0e3788f`; head `c91b2c1c7d633229914234502925010e93bdd341` [CI 42680025127](https://github.com/Osiris6900/osiris-eye/actions/runs/42680025127) and [audit 42680025148](https://github.com/Osiris6900/osiris-eye/actions/runs/42680025148) passed. | — | Status unchanged. Runs 42424830531, 42424833409, and 42430583187 predate current main and kept no node binary SHA-256. Later main commits do have successful recipient-only runs: three-host [42724204891](https://github.com/Osiris6900/osiris-eye/actions/runs/42724204891) at `8a531b37` and two-host [42717480948](https://github.com/Osiris6900/osiris-eye/actions/runs/42717480948) at `edae0ba3`. Current tip `9f9e27a` has no recipient-only run. #310 does not add one. |
| T04. Run sustained multi-host outage/load/relay/NAT acceptance | #176, #255 | Partial | Operator [capacity run 42740964830](https://github.com/Osiris6900/osiris-eye/actions/runs/42740964830) succeeded on exact main SHA `d9bb0a959faf5d9c7c97bd6502a143f9df9123d0`; three distinct GitHub runner jobs green. Retained `capacity-report-42740964830-1` ZIP SHA-256 `229553051305aabbc28c44af50bb58c4f6dfe9ec746ff3f2f85178333661d239`: 10/50/100 peers, 200/200 delivered and zero lost per sweep; report self-attests distinct host boot fingerprints and common binary hash. [#280](https://github.com/Osiris6900/osiris-eye/pull/280) merged at `d42e422edaa928e907f519de055a766e8e7e14ce` as local reconnect-tick backoff only. [CI 42677901050](https://github.com/Osiris6900/osiris-eye/actions/runs/42677901050) and [audit 42677901276](https://github.com/Osiris6900/osiris-eye/actions/runs/42677901276) passed on that commit. | — | Status stays Partial. #280 is local reconnect-tick backoff, not multi-host evidence. #176 and #255 stay open. **Partial with limits**: ~35–49 s/sweep, one tailnet, recipient-bootstrap star, workload paused during outage; raw fingerprints not independently verified. Sustained NAT/relay/open-mesh and production failover missing. Acceptance false. |
| T05. Relay-based operation (Circuit Relay v2 + HTTPS seed file), no public operator identity. Public-node deployment intentionally re-scoped. | #176, #181 | Implemented with evidence | Ghost-mode relay operation (Circuit Relay v2 + HTTPS seed file), no public operator identity. [Three-host run 42724204891](https://github.com/Osiris6900/osiris-eye/actions/runs/42724204891) includes successful non-recipient Host C; long-lived relay uptime and DR not evidenced. | — | Ghost-mode relay design is adopted; Host C non-recipient observer passed in run 42724204891. This does not prove long-lived relay uptime, public-node deployment, or incident recovery. #307 is evidence-shape only: dry-run [42685744834](https://github.com/Osiris6900/osiris-eye/actions/runs/42685744834) on merge `187f0c16a2afdb3a37bbe3950d841816d8fe5115` does not change this row. Next evidence is a staging recovery drill conforming to `docs/evidence/deploy-recovery.schema.json`. |
| T06. Commission independent cryptography and operational security review | #177, #181 | Remaining | `scripts/check_master_integration.py` defines the review shape and is not a passing CI gate. [#310](https://github.com/Osiris6900/osiris-eye/pull/310) is merged scaffolding; head [CI 42680025127](https://github.com/Osiris6900/osiris-eye/actions/runs/42680025127) and [audit 42680025148](https://github.com/Osiris6900/osiris-eye/actions/runs/42680025148) passed. | — | Status stays Remaining. #310's fail-closed crypto tests and recipient-hash harness are not an independent review. No independently signed reviewer report, verified reviewer identity, remediation/retest and approval decision has been supplied. |
| T07. Build authorized multi-asset vendor adapters and instrument master | #178 | Partial | [PR #243](https://github.com/Osiris6900/osiris-eye/pull/243) merged. Quant API CI [run 42467036806](https://github.com/Osiris6900/osiris-eye/actions/runs/42467036806) succeeded at `30f5f2a`. Main pytest again in [CI 42494850551](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850551). Code: `services/quant-api/src/osiris_api/authorized_feeds.py`, `instrument_master.py`. | — | The integration candidate implements Alpaca, OANDA, Polygon and Twelve Data bar adapters with bounded pagination, normalization and declared entitlement checks. Authorized vendor credentials, genuine captures, instrument/corporate-action coverage and independent source-owner acceptance remain pending. |
| T08. Implement streaming, point-in-time normalization, feed freshness and corporate actions | #178 | Partial | Freshness and admission code is covered by pytest in [CI 42494850551](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850551). [#309](https://github.com/Osiris6900/osiris-eye/pull/309) merged as scaffolding at `2f616c2258eafa220b34895ae5d123a4d7ff1907`. Head `7d0599fea4a20b9c454ef437a1dc44c7b01f981e` [CI 42679533894](https://github.com/Osiris6900/osiris-eye/actions/runs/42679533894) and [audit 42679534042](https://github.com/Osiris6900/osiris-eye/actions/runs/42679534042) passed. | — | Status stays Partial. #309 is scaffolding. Opened head `bd85dac` failed rust-audit [42676883926](https://github.com/Osiris6900/osiris-eye/actions/runs/42676883926) on RUSTSEC-2026-0459; the follow-up head is green. The rust job on that opened head succeeded, so a local Cargo 1.83 edition2024 parse error did not fail CI. Genuine sustained vendor streaming captures remain pending. |
| T09. Verify feed entitlements and redistribution rights | #178 | Remaining | Entitlement fields are fail-closed in `authorized_feeds.py` and `feed_admission.py`. | — | Vendor entitlement intake, expiry/revocation checks and feed-policy enforcement are implemented. Original source-owner licences, permitted-use/redistribution terms and independent rights approval have not been supplied. |
| T10. Integrate global event and macro feeds with source provenance | #178 | Partial | [PR #261](https://github.com/Osiris6900/osiris-eye/pull/261) merged. Tests on main run in [CI 42494850551](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850551). | — | Source-governed news/event intake, point-in-time storage and deduplication are implemented. Genuine authorized macro/event source integration, retained captures and independent provenance acceptance remain pending. |
| T11. Implement reproducible feature/model lifecycle and walk-forward gates | #178 | Partial | Walk-forward tests in `services/quant-api/tests/test_walk_forward_178.py`, [PR #243](https://github.com/Osiris6900/osiris-eye/pull/243), CI [run 42467036806](https://github.com/Osiris6900/osiris-eye/actions/runs/42467036806). | — | Point-in-time features, ridge fitting, immutable experiment reproduction and a retained synthetic walk-forward archive are implemented. A frozen genuine candidate dataset/model/feature archive and independent lifecycle acceptance remain pending. |
| T12. Validate strategies out of sample after all costs and leakage checks | #178 | Partial | `test_walk_forward_178.py` runs in [CI 42494850551](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850551) and checks a cost adjustment. | — | Chronological holdout, cost-adjusted walk-forward and reproducible experiment checks are implemented for research. A genuine blinded holdout, independent leakage review and calibrated cost/turnover/impact/borrow sensitivity report remain pending. |
| T13. Build independent portfolio risk budgets and strategy governance | #179 | Partial | `services/quant-api/src/osiris_api/risk.py` and `tests/test_paper_risk_acceptance_179.py`. [PR #258](https://github.com/Osiris6900/osiris-eye/pull/258) merged. Rust risk kernel and pytest pass in [CI 42494850551](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850551). | — | Canonical pretrade limits, exact broker economics, stale-feed/kill-switch controls and receipt-bound risk decisions are implemented. Independent ownership/approval of budgets, limits, concentration, drawdown and exception governance remains pending. |
| T14. Implement broker sandbox adapters, OMS state machine, idempotency and reconciliation | #179 | Partial | [#305](https://github.com/Osiris6900/osiris-eye/pull/305) is scaffolding, not acceptance. Opened at `fe90bae1778b7e30a17a5e991c9a585a71718b60`; merged `deed94692cd92096d84d8657e3d12034691d8466` (head `cb83b019ab5a897938623d052458c6b6d6f706a5`). [Quant API CI 42683473725](https://github.com/Osiris6900/osiris-eye/actions/runs/42683473725) and [dependency audit 42683473791](https://github.com/Osiris6900/osiris-eye/actions/runs/42683473791) passed on the merge commit. [CI 42683473981](https://github.com/Osiris6900/osiris-eye/actions/runs/42683473981) was cancelled. Main CI on that merge was cancelled. [CI 42748111800](https://github.com/Osiris6900/osiris-eye/actions/runs/42748111800) is the substitute: it passed python, rust, and web on `3a126f9a04e384229ade0313675c20ab90b44b49`, where `paper_execution.py` is unchanged since #305's merge. | — | #305 follows #277. T14 stays Partial: no broker-sandbox match. The daily-loss gate includes adverse unrealized loss (`daily_loss_basis` is `realized_plus_adverse_unrealized`) and fails closed when another holding has no fresh mark. [#439](https://github.com/Osiris6900/osiris-eye/pull/439) merged as scaffolding at `71b3d4de0a85edd28bd1a6ec0f5457af58c14558` (head `906d53942a8ce682ecb6e7d0267b41016709a289`). Head [CI 43493100907](https://github.com/Osiris6900/osiris-eye/actions/runs/43493100907) passed python, rust, and web; [audit 43493101800](https://github.com/Osiris6900/osiris-eye/actions/runs/43493101800) passed rust-audit; [Quant API CI 43493102241](https://github.com/Osiris6900/osiris-eye/actions/runs/43493102241) and [179 contract 43493100925](https://github.com/Osiris6900/osiris-eye/actions/runs/43493100925) passed. Merge [CI 43494481576](https://github.com/Osiris6900/osiris-eye/actions/runs/43494481576) passed python, rust, and web. [#442](https://github.com/Osiris6900/osiris-eye/pull/442) merged as scaffolding at `78818b92618e9e230f4a9fa4c7e42fe3be9fd553` (head `f7ca859c576e2d5403e6c1c5f569aea6e01a5733`). Head [CI 43501436283](https://github.com/Osiris6900/osiris-eye/actions/runs/43501436283), [audit 43501436218](https://github.com/Osiris6900/osiris-eye/actions/runs/43501436218), and [Quant API CI 43501436401](https://github.com/Osiris6900/osiris-eye/actions/runs/43501436401) passed. Buys project exposure at a fresh higher mark. Live execution stays disabled. Two local failures on unmodified main are environmental: the restricted worker fails until `osiris_api` is installed for `python -I`, and the receipt bridge fails on Cargo 1.83 because `edition2024` is required. Head CI passed both. |
| T15. Add realistic paper fills, positions, cash, margin and P&L accounting | #179 | Partial | The same durable-ledger tests assert cash, position quantity, and realized P&L in [CI 42494850551](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850551). | — | Canonical cash/positions, partial fills/cancellation, fee-inclusive realized/unrealized P&L and bounded adverse slippage are implemented and tested. Venue-calibrated liquidity/queue/impact, margin/borrow models and independent multi-asset execution comparison remain incomplete. |
| T16. Prove kill-switch and stale-feed fail-closed behavior | #179, #180 | Partial | Merged PRs [#326](https://github.com/Osiris6900/osiris-eye/pull/326) and [#329](https://github.com/Osiris6900/osiris-eye/pull/329) harden paper workflow approval and order-intent binding. The T16 CI evidence-contract workflow now exercises real code paths with **isolated temporary synthetic SQLite** kill-switch, stale observation and crash/restart scenarios and retains a 14-day paper-failure-matrix report when green. [#309](https://github.com/Osiris6900/osiris-eye/pull/309) merged as scaffolding; head [CI 42679533894](https://github.com/Osiris6900/osiris-eye/actions/runs/42679533894) and [audit 42679534042](https://github.com/Osiris6900/osiris-eye/actions/runs/42679534042) passed, and merge [CI 42684177898](https://github.com/Osiris6900/osiris-eye/actions/runs/42684177898) passed. | — | Status stays Partial. #309 does not accept the kill-switch proof. Independent service-outage acceptance and final-candidate external operational/risk review remain pending. |
| T17. Build authenticated operator dashboard for peers, feeds, orders, risk and audit | #180 | Partial | [PR #245](https://github.com/Osiris6900/osiris-eye/pull/245) merged. Quant API CI [run 42471703916](https://github.com/Osiris6900/osiris-eye/actions/runs/42471703916) and web job in [CI 42494850551](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850551). Code: `admin_centre.py`, `apps/web/src/AdminCentre.tsx`. [#308](https://github.com/Osiris6900/osiris-eye/pull/308) head `3975e8561318a8bb9c360b01aff2d0c2b941861e` has green [CI 42679237031](https://github.com/Osiris6900/osiris-eye/actions/runs/42679237031). [#438](https://github.com/Osiris6900/osiris-eye/pull/438) merged as scaffolding at `b57ff6bea6795cdb7d374721e2b99eec4a30862a` (head `22c829c4795668fbcac486283dcbe2e5c041cc2b`). [CI 43486289288](https://github.com/Osiris6900/osiris-eye/actions/runs/43486289288), [Quant API CI 43486289127](https://github.com/Osiris6900/osiris-eye/actions/runs/43486289127), [audit 43486289381](https://github.com/Osiris6900/osiris-eye/actions/runs/43486289381), [179 contract 43486289393](https://github.com/Osiris6900/osiris-eye/actions/runs/43486289393), and [182 contract 43486289124](https://github.com/Osiris6900/osiris-eye/actions/runs/43486289124) passed. | — | #438 is scaffolding for the read-only monitoring panel. The pull request says this is not accepted operations evidence, so T17 stays Partial. [#440](https://github.com/Osiris6900/osiris-eye/pull/440) merged as scaffolding at `35fd514d06f7d897b1aa68be792f3b803abd69dc` (head `b4a4130c530f2ddb2c0d42c74271553b73f970c0`). [CI 43495262061](https://github.com/Osiris6900/osiris-eye/actions/runs/43495262061), [Quant API CI 43495261981](https://github.com/Osiris6900/osiris-eye/actions/runs/43495261981), [audit 43495262176](https://github.com/Osiris6900/osiris-eye/actions/runs/43495262176), [179 contract 43495262506](https://github.com/Osiris6900/osiris-eye/actions/runs/43495262506), and [182 contract 43495262035](https://github.com/Osiris6900/osiris-eye/actions/runs/43495262035) passed. The pull request says this does not move T17 to accepted operations evidence, so T17 stays Partial. |
| T18. Add metrics, alerts, distributed persistence, backups and disaster recovery | #181, #180 | Partial | In-process alert registry covered with the admin API tests in [run 42471703916](https://github.com/Osiris6900/osiris-eye/actions/runs/42471703916). | #308 | Actual temporary SQLite backup/restore/continuation, cross-runner integrity intake, authority fencing and rollout/rollback checks are implemented. Approved independent-host service DR with measured RTO/RPO, accepted custody/storage and operator signoff remain pending. #307 is scaffolding only. Next evidence is a real staging recovery drill artifact conforming to `docs/evidence/deploy-recovery.schema.json`. #308 is related scaffolding for alert history, closed without a merge, and does not implement T18. |
| T19. Run capacity/failover and CI release gates with traceable artifacts | #181, #182 | Partial | Operator [three-host capacity run 42740964830](https://github.com/Osiris6900/osiris-eye/actions/runs/42740964830) on `d9bb0a959faf5d9c7c97bd6502a143f9df9123d0`, retained `capacity-report-42740964830-1` ZIP SHA-256 `229553051305aabbc28c44af50bb58c4f6dfe9ec746ff3f2f85178333661d239`, 3 green host jobs; earlier [CI 42494850551](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850551) and [audit 42494850772](https://github.com/Osiris6900/osiris-eye/actions/runs/42494850772). | — | Short historical one-tailnet capacity evidence remains limited to the recorded topology. Source-bound local Rust E2EE receipt -> kernel-pinned Python durable ledger -> current risk -> canonical paper order/fill -> P&L -> forced process loss/new-process recovery is now implemented. Sustained independent-host capacity/failover, broader DR and final-candidate operational acceptance remain pending. #307's dry-run does not supply that evidence. Next evidence is a staging recovery drill artifact conforming to `docs/evidence/deploy-recovery.schema.json`. #436 is supporting CI evidence for engineering verification and the #182 contract: head [CI 43416302724](https://github.com/Osiris6900/osiris-eye/actions/runs/43416302724) and [182 evidence contract 43416302491](https://github.com/Osiris6900/osiris-eye/actions/runs/43416302491) passed on `666a3e5599363f6347e671664b8135aeab0d38e6`, merge `9f9e27a3cf286cb3bb95cf33b5a245ec2b413118`. It does not accept T19. Dependency Security Audit [42679453468](https://github.com/Osiris6900/osiris-eye/actions/runs/42679453468) passed on `26fd658f9f49463fca36e8a0eedc0344881b046a` and is supporting CI evidence for this row. |
| T20. Perform compliance, model-risk and security signoff for paper-only release | #182, #207 | Remaining | No signoff artifact. | — | The paper-only signoff worksheet and external security/vendor intake paths are implemented. Actual independent security/model-risk/licensing decisions and authorized operator release approval remain pending. Paper-only scope is unchanged and live execution remains unauthorized. |

Historical 2026-11-08 baseline counts: 1 Implemented with evidence, 13 Partial, 6 Remaining. Updated inventory after the 2026-11-10 evidence review: **3 Implemented with evidence, 14 Partial, 3 Remaining**. This count does not authorize paper execution or live trading.

## Archived gate PR review (historical snapshot, 2026-11-08)

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
| [#281](https://github.com/Osiris6900/osiris-eye/pull/281) #171 residual boundaries | Open, not draft. Git base is #276's branch. | T01 | Docs only (`docs/workstreams/ISSUE-171.md`). Does not change the lock or clear paste. |
| [#277](https://github.com/Osiris6900/osiris-eye/pull/277) paper kill switch and exposure | Open, not draft, unreviewed. Copies the #268 test file unchanged. | T13, T14, T15, T16 | Code, not acceptance, until it merges and CI passes. Adds a paper book with kill switch, stale-data refusal, and an internal `reconcile()`. `broker_order_submitted` stays false. It is not a broker OMS. |
| [#278](https://github.com/Osiris6900/osiris-eye/pull/278) admin fills and outbox | Open, not draft, unreviewed. | T15, T17 | Read-only display of stored synthetic fills and outbox counts. `open_orders` is null. `peer_count` is null. Does not prove peer telemetry. |
| [#279](https://github.com/Osiris6900/osiris-eye/pull/279) rejection-log throttle | Open, not draft, unreviewed. | T04 | Adjusts `peer_not_allowed` log lines and soak parsing. The allowlist still drops the message. It does not run a 100-peer independent-host soak. |
| [#280](https://github.com/Osiris6900/osiris-eye/pull/280) bootstrap/relay redial backoff | Open, not draft. Git base is #279's branch, not `main`. | T04 | Backs off redials to a down bootstrap or relay. No multi-host soak and no binary hashes. |
| [#283](https://github.com/Osiris6900/osiris-eye/pull/283) reconnect dial budget | Open, not draft. Git base is #280's branch. | T04 | Caps how many bootstrap peers one reconnect tick dials. No multi-host soak. |

## Recommended merge order

1. **#276** (#171). Lock-floor ratchet only. `Cargo.lock` stays as it is. Paste and the SBOM marker keep T01 Partial. **#281** is stacked on #276 and only updates `docs/workstreams/ISSUE-171.md`. It does not clear T01.
2. **#279** (#255, #176) before any new soak run, so the soak parses the throttle lines this PR emits. It does not close T04.
3. **#280** (#176) after #279. Its git base is that branch. Reconnect backoff only. It does not close T04. **#283** is stacked on #280 and only caps dials per reconnect tick.
4. **#277** (#179) ahead of #268. It already contains that test file unchanged. It is the paper-path kill switch and stale-data work. T14 and T16 stay Partial until the merge commit's CI is green, and T14 still lacks a broker.
5. **#268** only if #277 is not merged. Merging both will touch the same new test file.
6. **#278** (#180) before #271. Read-only fills and outbox status. `peer_count` stays null, so T17 stays Partial.
7. **#272** (#181). Isolated staging-config script. It does not close T18.
8. **#270** (#177) only after a reviewer checks it against the existing receipt and revocation path.
9. **#271** (#180) after #278, and only if it uses the `admin_centre` permission tables.
10. **#273** (#178) as an eligibility function. The merge note should say it leaves T12 Partial.
11. **#264** (#178) as an offline feed validator. The merge note should say it leaves T09 Remaining.
12. **Hold #274** (#182) until host binding uses `measurement_class` from `scripts/run_p2p_soak.py`.
13. **#265 last** (#179). Read it beside both `durable_paper_ledger.py` and #277's `paper_execution.py` so two paper books are not merged blindly. It does not create broker OMS reconciliation.
14. **#269 optional** (#176, #255). Equivalent classification cases already run in CI.

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


## Addendum — 2026-11-10: verified operator-dispatched 10/50/100-peer capacity evidence

This supplement records a **real successful operator workflow_dispatch run**, not a schema-only or local-process simulation. The original November 8 PR snapshots below remain historical context, not a current open-PR list.

- Run: [42740964830](https://github.com/Osiris6900/osiris-eye/actions/runs/42740964830); event `workflow_dispatch`, conclusion `success`, attempt 1; exact main commit `d9bb0a959faf5d9c7c97bd6502a143f9df9123d0`.
- Jobs: `Host A - sender/controller`, `Host B - intended recipient`, `Host C - non-recipient observer` each completed `success`.
- Retained artifact: `capacity-report-42740964830-1` (GitHub artifact ID `13728943887`, expires 2026-11-24T02:20:22Z). Downloaded ZIP SHA-256: `229553051305aabbc28c44af50bb58c4f6dfe9ec746ff3f2f85178333661d239`.
- Independently recomputed JSON SHA-256: `capacity-10.json` `e2d613b98d6937c0e6ab9244d6477752c45bce217c472088657060043679486c`; `capacity-50.json` `478da0b9fdd9c47c6b5064a3c8c04f83c9eaf70c97e2b2ed484b33fbf31bd1d2`; `capacity-100.json` `54d05a743097ab4702e9b21b43f06888380c13093515ca73fd17e1087606dd91`.
- All three reports bind to the same run/attempt/main commit and state `clean_worktree=true`, `measurement_class=operator_attested_real_multi_host`, `open_mesh_capacity_tested=false` and `independent_host_fingerprints_distinct=true` **as a self-attestation**. Binary digest for all 3 hosts in each report: `ce00434c26c9ef03cdd42a5faeade0974facb7a92f988d0239a81da812af90d1`.
- Recomputed from downloaded files: each of the three sweeps has 200 distinct payload hashes, 200 delivered observations, zero lost, finite nonnegative recorded latencies and both disconnected and recovered outage samples. No raw host boot identifiers were included to independently verify the distinctness claim.

| Peers | Measured sweep duration | Sent/delivered/lost | p50 ms | p95 ms | p99 ms |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 35.44 s | 200 / 200 / 0 | 3.58 | 45.78 | 101.51 |
| 50 | 48.62 s | 200 / 200 / 0 | 3.40 | 39.97 | 43.84 |
| 100 | 48.65 s | 200 / 200 / 0 | 3.35 | 100.56 | 187.00 |

**Acceptance limits:** three short CI runner sweeps on one private tailnet, fanout 1, recipient-bootstrap star. Controller latency includes SSH observer transport. Workload was drained/paused for the bootstrap outage; this does not prove in-flight durability, a sustained traffic-on failover, independent NAT traversal, public relay uptime, broad geographic deployment, or true open-mesh capacity. No independently signed security or model/compliance approval exists. Therefore **T04 moves from Remaining to Partial; T19 stays Partial; both `acceptance_satisfied=false`**. Do not close #322/#323 or treat this as authorization for live trading; **PAPER-ONLY indefinitely**.


## 2026-11-10 addendum — T16 isolated failure-injection test contract

The existing `179-evidence-contract` workflow now runs `scripts/run_paper_failure_exercise.py` against actual paper-ledger code in three disposable SQLite databases, followed by `scripts/check_paper_failure_matrix.py`. On successful CI it retains a 14-day `paper-failure-matrix-<run-id>-<attempt>` artifact with a JSON scenario report and SHA-256 manifest. This is **test infrastructure, not accepted proof at documentation authoring time**; capture the exact SHA, Actions URL, real job conclusion and independently verified artifact digest before citing a successful run. It does not represent service-level failover, exchange submission, real-world recovery testing or independent compliance review. T16 stays **Partial** and PAPER-ONLY indefinitely.


## 2026-11-10 addendum — Tailscale Host B artifact-download race (CI infrastructure)

- [Tailscale cross-host Actions run 42756305177](https://github.com/Osiris6900/osiris-eye/actions/runs/42756305177) failed in Host B at `Download Host A endpoint`. Host A's same-run artifact `p2p-endpoint-42756305177-1` was already listed (artifact ID `13736295435`) when Host B attempted its first download, but GitHub returned `Unable to download artifact(s): Not Found` about one second after publication. The logs indicate a **likely artifact-index vs backing-blob visibility race**; this is not evidence of a failed P2P handshake or cryptographic vulnerability.
- The Tailscale workflow now **retries reading actual same-run, same-attempt artifact bytes** using `gh run download` for both Host A endpoint and Host A completion status; each attempt is time-bounded, and the entire retry budget is bounded to 600 seconds. Downloaded JSON has a minimal shape/content validation before being atomically installed for the next step. If the blob remains missing, malformed or undownloadable, the step fails closed rather than treating a listed artifact as successful acceptance.
- No new cryptographic peer authorization or network connection is implied by this CI transport fix. The existing run cannot be retroactively repaired; a fresh post-merge run must demonstrate Host B download, TCP diagnosis, handshake and the required 20-message integrity test.
- Immutable GitHub Actions pin inventory: **18 workflows, 111 pinned action uses, 0 mutable** after replacing two direct `actions/download-artifact` invocations with verified Python/`gh` retries. Gate statuses remain unchanged; PAPER-ONLY indefinitely, ghost-mode.
# 2026-11-12 inventory consistency correction

PR #408 at `1a202929cde2db15a02615a9428b784019222ab5` failed the
release inventory job because `facts.advisory_source_commit` still referenced
`2501a07441c7a3e124a7494f2384903c85f5b6ca` after the dependency advisory was
refreshed from `d479d2e5ac2215faf55c61332fa80a3c5ae39b2b`. The checked-in
inventory now matches the advisory's actual provenance. Advisory input hashes,
workflow/action counts, gate statuses and acceptance evidence are unchanged.
The inventory checker and its eight regression tests pass locally. This fixes
inventory consistency only; the release remains 3 evidenced, 14 partial and
3 remaining, with `live_trading_authorized=false`.
