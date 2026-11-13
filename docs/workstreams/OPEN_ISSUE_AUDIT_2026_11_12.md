# Open-issue engineering audit — 2026-11-12

Snapshot: **46 open issues** in `Osiris6900/osiris-eye`, inspected against main
`5b43e61c8c974ef4e52b22c32dbd9cba17ccc62c` after integration PR #408 merged.
This is a scope and evidence map. **No issue is closed or claimed fully accepted.**
The release inventory remains 3 evidenced / 14 partial / 3 remaining, with 17
unaccepted release gates.

## Reproducible defects addressed in this patch

- Calendar status selected UTC dates instead of venue-local dates, missed eastern
  sessions and western overnight sessions, confused known closed days with
  unavailable data, and accepted ambiguous DST/local-offset times.
- Calendar `:memory:` operations lost the schema/data between connections.
  Operations now share an isolated anchored database and close their own connections.
  Empty/non-string timestamps fail validation instead of becoming the current time
  or raising an attribute error. Legacy ambiguous sessions are also refused on read.
- An expired IAM session could become valid again after the server clock moved
  backwards. A durable identity-clock high-water mark now rejects observed
  rollback across restarts; invalid/nonfinite clocks cannot authenticate.
  Session issuance uses the same clock sample as successful TOTP verification.
- Feature timestamps and signed replication sequences/cursors accepted integers
  outside SQLite's signed 64-bit range. Validation now rejects them before SQL
  binding and before any replication batch write.
- Market views limited database rows before applying current rights/freshness.
  Newer ineligible records could hide an older eligible source. Reads now stream
  the same transaction, count eligible results toward the requested limit and stop
  beyond the longest permitted freshness window, preserving sub-millisecond checks.

These changes cover concrete defects within #363, #368, #373, #377, #378,
#386, #392 and #397 and their umbrella issues. They do not complete the whole
acceptance scope of any of those issues.

## Verification

Run the existing full engineering profile, which exercises Python, Rust,
frontend, local receipt-to-paper processes, disposable recovery, research
archives, source/reviewer intake and Chromium/HTTPS workers:

```bash
python scripts/verify_all.py --profile engineering --execute --report /tmp/osiris-engineering.json
```

It requires the project's Python environment, Node >=24, Rust and Chromium on PATH,
installed locked dependencies and permission for local test sockets. Reports bind
the source snapshot, actual outputs and artifact hashes. A pass means local
engineering checks only; skipped/missing checks and source changes are not passes.
The final PR records the actual validation result.

In the table below, `control_plane/...` and unprefixed Python filenames are
relative to `services/quant-api/src/osiris_api/`. Named engineering checks refer
to `scripts/verify_all.py`; offline advisor checks are the corresponding
`scripts/test_*.py` suites. Source references identify existing code, including
prototype/advisory implementations, and do not certify the issue's full scope.

## Complete issue inventory

| Issue | Existing implementation / entry points | Local verification | Remaining scope or acceptance |
|---|---|---|---|
| [#322](https://github.com/Osiris6900/osiris-eye/issues/322) T18 backups/DR evidence run | `scripts/run_paper_dr_exercise.py`; `scripts/verify_paper_dr_artifacts.py` | local-paper-dr, local-dr-integrity, portable-recovery | Operator-approved independent-host DR, measured RTO/RPO and retained real artifacts. |
| [#323](https://github.com/Osiris6900/osiris-eye/issues/323) Independent cryptography review commission | `scripts/check_external_crypto_review_intake.py` | security-intake | Named independent cryptography/operations reviewer, signed findings and retest. |
| [#348](https://github.com/Osiris6900/osiris-eye/issues/348) Release execution: verify and close 17 remaining gates across all workstreams | `docs/release-gate-status.json`; `scripts/verify_all.py` | All engineering checks; strict inventory remains unsatisfied | 17 release gates still require accepted evidence; all-green local tests cannot close them. |
| [#363](https://github.com/Osiris6900/osiris-eye/issues/363) [Integration] Dashboard backend integration | `apps/web/src/DashboardWorkspace.tsx`; `control_plane/api.py`; `control_plane/market_views.py` | frontend-tests, frontend-build, browser, python-tests | Independent UI/data acceptance and genuine source configuration. This patch fixes eligible records hidden by response limits. |
| [#364](https://github.com/Osiris6900/osiris-eye/issues/364) [Integration] Paper account consistency | `admin_centre.py`; `paper_execution.py`; `control_plane/accounting.py` | python-tests, market-paper, receipt-paper | Independent account reconciliation and separately scoped legacy-history import. |
| [#365](https://github.com/Osiris6900/osiris-eye/issues/365) [Integration] P2P per-peer intelligence telemetry | `control_plane/peer_telemetry.py`; `services/p2p-node/src` | rust-p2p, peer-receipts, python-tests | Independent-node telemetry, sustained host-boundary and fault acceptance. |
| [#366](https://github.com/Osiris6900/osiris-eye/issues/366) [Integration] P2P network acceptance evidence | `scripts/test_p2p_three_host_e2ee_ssh.py`; `scripts/check_p2p_capacity_evidence.py` | rust-p2p, peer-receipts; offline harness tests | Authorized hosts, full-duration NAT/relay/open-mesh workload and signed retained evidence. |
| [#367](https://github.com/Osiris6900/osiris-eye/issues/367) [Integration] Rust receipt to Python ledger to paper order E2E | `scripts/run_receipt_paper_acceptance.py`; `paper_receipt_execution.py` | receipt-paper, market-paper, python-tests | Independent operational acceptance of the exact final commit and retained trace artifacts. |
| [#368](https://github.com/Osiris6900/osiris-eye/issues/368) [Integration] Market data provider readiness | `control_plane/vendors.py`; `authorized_feeds.py`; `control_plane/market_views.py` | python-tests, vendor-intake | Vendor credentials, original rights, genuine stream/macro/corporate-action captures. Eligibility-limit fix included. |
| [#369](https://github.com/Osiris6900/osiris-eye/issues/369) [Integration] Model OOS and independent risk approval | `control_plane/models.py`; `control_plane/experiments.py`; `walk_forward.py` | python-tests, model-archive | Frozen genuine datasets, blinded OOS/leakage review and independent risk approval. |
| [#370](https://github.com/Osiris6900/osiris-eye/issues/370) [Integration] Paper execution realism | `control_plane/broker.py`; `control_plane/depth.py`; `paper_execution.py` | python-tests, market-paper, receipt-paper, rust-risk | Venue/queue/impact/margin/borrow calibration and authorized paper-broker comparison. |
| [#371](https://github.com/Osiris6900/osiris-eye/issues/371) [Integration] Independent recovery and release signoff | `scripts/verify_cross_runner_dr_artifacts.py`; `scripts/check_external_crypto_review_intake.py`; `docs/release/paper-only-signoff.md` | recovery-intakes, security-intake, vendor-intake | Independent hosts, original reports/licences and owner release decisions. |
| [#373](https://github.com/Osiris6900/osiris-eye/issues/373) [Platform hardening] Identity and access management | `control_plane/identity.py`; `control_plane/api.py` | python-tests, browser | This patch prevents expired-session revival after observed clock rollback and rejects invalid clocks. Independent IAM/account-recovery review remains. |
| [#374](https://github.com/Osiris6900/osiris-eye/issues/374) [Platform hardening] Secrets and key management | `control_plane/secrets.py`; `control_plane/identity.py` | python-tests, security-intake | External master-key custody, deployed rotation/Vault acceptance and independent review. |
| [#375](https://github.com/Osiris6900/osiris-eye/issues/375) [Platform hardening] High availability and split-brain safety | `control_plane/coordination.py` | python-tests, recovery-intakes | Independent-host partition/failover evidence; SQLite authority is not quorum consensus. |
| [#376](https://github.com/Osiris6900/osiris-eye/issues/376) [Platform hardening] Observability control centre | `control_plane/observability.py`; `control_plane/api.py` | python-tests, browser | Configured exporter delivery, retention, SLOs and real alert operations. |
| [#377](https://github.com/Osiris6900/osiris-eye/issues/377) [Platform hardening] Data quality engine | `feed_quality_gate.py`; `control_plane/market_views.py`; `scripts/market_data_quality_clock_guard.py` | python-tests; offline quality-guard tests | This patch applies record limits after policy/freshness filtering. Genuine missing-tick/conflict/outlier acceptance remains. |
| [#378](https://github.com/Osiris6900/osiris-eye/issues/378) [Platform hardening] Time synchronization and event ordering | `control_plane/clock.py`; `control_plane/identity.py`; `control_plane/coordination.py` | python-tests | This patch fences identity time across restarts. Authenticated time sources and independent-host drift evidence remain. |
| [#379](https://github.com/Osiris6900/osiris-eye/issues/379) [Platform hardening] Disaster recovery automation | `scripts/run_paper_dr_exercise.py`; `scripts/check_paper_cross_runner_restore.py` | local-paper-dr, local-dr-integrity, portable-recovery, recovery-intakes | Operator-approved independent-host recovery and measured production-relevant RTO/RPO. |
| [#380](https://github.com/Osiris6900/osiris-eye/issues/380) [Platform hardening] Security verification | `scripts/generate_dependency_sbom.py`; `scripts/check_external_crypto_review_intake.py` | supply-chain-sbom, security-intake, python-tests, rust-p2p | External penetration/crypto review, target-specific fuzz/property evidence and key custody. |
| [#381](https://github.com/Osiris6900/osiris-eye/issues/381) [Platform hardening] Strategy lifecycle management | `control_plane/models.py`; `control_plane/experiments.py`; `control_plane/shadow.py` | python-tests, model-archive | Independent model approvals, genuine data and deployed rollback acceptance. |
| [#382](https://github.com/Osiris6900/osiris-eye/issues/382) [Platform hardening] Capital allocation controls | `phase10_systems.py`; `risk.py`; `paper_execution.py` | python-tests, rust-risk, market-paper | Approved strategy budgets/limits and independent allocation/risk acceptance. |
| [#383](https://github.com/Osiris6900/osiris-eye/issues/383) [Platform hardening] Compliance and surveillance | `phase7_market_intelligence.py`; `phase15_audit_chain.py`; `control_plane/custody.py` | python-tests, security-intake | Jurisdiction-specific legal/compliance review and accepted immutable custody. |
| [#384](https://github.com/Osiris6900/osiris-eye/issues/384) [Platform hardening] Deployment control centre | `control_plane/deployment.py` | python-tests | Authorized staging target, signed release, real canary/rollback and operator acceptance. |
| [#386](https://github.com/Osiris6900/osiris-eye/issues/386) [Advanced platform] Global market calendar | `exchange_calendar.py`; `calendar_routes.py` | python-tests: test_exchange_calendar.py | This patch fixes local-date/overnight lookup, closed-day availability, DST ambiguity, timestamp validation and memory persistence. Official exchange schedules, halt windows and independent coverage remain. |
| [#387](https://github.com/Osiris6900/osiris-eye/issues/387) [Advanced platform] Cross-asset exposure graph | `phase9_systems.py`; `phase6_intelligence.py` | python-tests | Verified instruments/prices/FX and a complete persisted cross-asset exposure graph. |
| [#388](https://github.com/Osiris6900/osiris-eye/issues/388) [Advanced platform] Portfolio stress testing | `phase3_systems.py`; `phase10_systems.py`; `scripts/advanced_risk_signal_advisors.py` | python-tests; offline risk-advisor tests | Calibrated historical/hypothetical shocks, liquidity/correlation/FX scenarios and independent risk review. |
| [#389](https://github.com/Osiris6900/osiris-eye/issues/389) [Advanced platform] Execution transaction cost analysis | `control_plane/accounting.py`; `phase10_systems.py` | python-tests, market-paper, receipt-paper | Genuine arrival/VWAP/venue timing and independently calibrated execution-quality benchmarks. |
| [#390](https://github.com/Osiris6900/osiris-eye/issues/390) [Advanced platform] Market regime detection | `control_plane/market_views.py`; `mesh_market_intelligence.py` | python-tests, browser | Genuine market histories, calibrated regime/model validation and liquidity observations. |
| [#391](https://github.com/Osiris6900/osiris-eye/issues/391) [Advanced platform] Signal conflict resolver | `scripts/advanced_risk_signal_advisors.py` | offline risk-advisor tests | Production-quality confidence/recency/risk policy, lineage and independent resolver approval; existing resolver is advisory. |
| [#392](https://github.com/Osiris6900/osiris-eye/issues/392) [Advanced platform] Feature store and lineage | `control_plane/features.py`; `control_plane/models.py` | python-tests | This patch rejects out-of-range SQLite timestamps. Genuine datasets, verified availability provenance and distributed serving remain. |
| [#393](https://github.com/Osiris6900/osiris-eye/issues/393) [Advanced platform] Model drift monitor | `phase6_intelligence.py`; `scripts/model_drift_shadow_guard.py` | python-tests; offline drift-guard tests | Genuine model/feature monitoring, calibrated thresholds and independently accepted disable/rollback operations. |
| [#394](https://github.com/Osiris6900/osiris-eye/issues/394) [Advanced platform] Shadow trading engine | `control_plane/shadow.py` | python-tests, market-paper, paper-failures | Calibrated venue behavior and independent paper/live-reference comparison; no live execution authorized. |
| [#395](https://github.com/Osiris6900/osiris-eye/issues/395) [Advanced platform] Portfolio attribution | `control_plane/accounting.py`; `paper_execution.py` | python-tests, market-paper, receipt-paper | Independent broker reconciliation and multi-asset unrealized/FX attribution. |
| [#396](https://github.com/Osiris6900/osiris-eye/issues/396) [Advanced platform] Liquidity intelligence | `control_plane/depth.py`; `crates/native-feed/src` | python-tests, rust-native, native-process | Genuine licensed depth/streaming and venue/queue/multi-venue calibration. |
| [#397](https://github.com/Osiris6900/osiris-eye/issues/397) [Advanced platform] Distributed state reconciliation | `control_plane/replication.py` | python-tests | This patch bounds replication sequence/cursors before SQLite writes. Independent-node convergence and financial-state consensus remain. |
| [#398](https://github.com/Osiris6900/osiris-eye/issues/398) [Advanced platform] Network trust and reputation | `scripts/p2p_network_intelligence.py`; `control_plane/peer_telemetry.py`; `services/p2p-node/src` | rust-p2p, peer-receipts; offline network-advisor tests | Accepted authenticated reputation/decay/quarantine policy; advisory rankings do not establish peer trust. |
| [#399](https://github.com/Osiris6900/osiris-eye/issues/399) [Advanced platform] Operational command centre | `control_plane/observability.py`; `apps/web/src/DashboardWorkspace.tsx` | python-tests, frontend-tests, browser | Unified incident/recovery workflows, node-health operations and independent operator acceptance. |
| [#400](https://github.com/Osiris6900/osiris-eye/issues/400) [Advanced platform] Research experiment registry | `control_plane/experiments.py` | python-tests: test_experiment_registry.py; model-archive | Immutable local reproduction exists on main; genuine datasets and independent model/registry acceptance remain. |
| [#405](https://github.com/Osiris6900/osiris-eye/issues/405) [NETWORK MASTER] End-to-end P2P hardening, acceptance and release blockers | `services/p2p-node/src`; `scripts/run_receipt_paper_acceptance.py`; `docs/workstreams/WORKSTREAM_DELIVERY_2026_11_12.md` | rust-p2p, peer-receipts, receipt-paper, recovery-intakes | Sustained independent-host NAT/relay/open mesh, outage/DR, cryptography review and operator signoff. |
| [#409](https://github.com/Osiris6900/osiris-eye/issues/409) [INTEGRATION MASTER] Seven parallel acceptance tracks after platform20 MVP (#408) | `docs/workstreams/WORKSTREAM_DELIVERY_2026_11_12.md`; `scripts/verify_all.py` | All engineering checks | PR #408 is merged at baseline. Seven integration tracks still require independent data/model/network/operations acceptance. |
| [#412](https://github.com/Osiris6900/osiris-eye/issues/412) Parallel implementation: AI signals, risk, paper E2E, licensed feeds, P2P and operator dashboard | `paper_pipeline.py`; `control_plane/api.py`; `apps/web/src/DashboardWorkspace.tsx` | All engineering checks | Licensed feeds, independent OOS/risk decisions, sustained host tests and external release acceptance. |
| [#413](https://github.com/Osiris6900/osiris-eye/issues/413) Institutional hardening: backtests, ML lifecycle, routing, optimization, microstructure, events, storage, DR, security and observability | `control_plane`; `scripts/run_paper_dr_exercise.py`; `scripts/generate_dependency_sbom.py` | All engineering checks | Genuine backtests/ML/routing/storage/observability deployments and independent DR/security validation. |
| [#414](https://github.com/Osiris6900/osiris-eye/issues/414) Platform expansion: exchanges, derivatives, arbitrage, alternative data, regime, simulation, treasury, tenancy, IAM, compliance, billing, release | `docs/workstreams/WORKSTREAM_DELIVERY_2026_11_12.md`; `control_plane` | Existing engineering checks cover only implemented portions | Additional product work remains: tenant isolation, billing/privacy/treasury/exchange coverage and independent jurisdiction/model approval. |
| [#415](https://github.com/Osiris6900/osiris-eye/issues/415) Advanced R&D: distributed/federated AI, causal research, adversarial simulation, agents, marketplace, acceleration, zero trust, lineage, formal methods | `control_plane/agents.py`; `control_plane/coordination.py`; `control_plane/features.py`; `crates/native-feed/src` | Existing engineering checks cover only implemented portions | Federated/distributed training, causal/adversarial research, formal proofs, marketplace/payment and OS-isolation work are not completed by local primitives. |
| [#416](https://github.com/Osiris6900/osiris-eye/issues/416) Remaining-work master backlog: data quality, OMS/EMS, risk, audit, resilience and commercial readiness (39–70) | `docs/workstreams/WORKSTREAM_DELIVERY_2026_11_12.md`; `scripts/verify_all.py` | All engineering checks; quality, identity, feature/replication and calendar regressions added | Backlog 39–70 includes unfinished tax, commercial/vendor contracts, support/onboarding, scale/chaos/SLO and independent review work. |

## Dependencies that code and local tests cannot supply

Original vendor licences/credentials and genuine captures; independently hosted
NAT/relay/HA/DR targets and sustained workloads; independent signed crypto,
security, model, risk, legal/compliance and operator release decisions. These
must be supplied or accepted by their respective owners. No external message,
paid service, production deployment or live order is part of this patch.

Additional engineering in #387, #391, #398, #399 and the expansion packages
#413–#416 is also still incomplete; these are not merely paperwork blockers.
Do not substitute advisory functions or checklists for working products, or infer
financial consensus from event replication and a SQLite lease authority.

Live trading remains disabled. Release inventories and issue states are unchanged.
