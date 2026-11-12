# Workstream audit — 12 November 2026

The workstreams are **not all complete**. The release inventory records **3
implemented with evidence, 14 partial and 3 remaining**. The ordinary inventory
checker passes; `--strict` refuses acceptance for T01, T04 and T06–T20. These
are acceptance gates, not a percentage of code completion.

Main at inspection was `ff7ba8e44dbfb96327457969e9aa1eae62fe0cdf`.
The twenty-workstream integration is still in [PR #408](https://github.com/Osiris6900/osiris-eye/pull/408),
head `cb5be6bea4dceef9a8ddefc33fafcca53c41bfd4`, awaiting independent review.
This audit and its fixes build on that candidate. They do not silently merge it
into main or accept an external release gate.

## Defects corrected in this audit

| Workstream | Defect | Corrected behavior |
|---|---|---|
| W02 / decimal consumers | A short input such as `1e-999999999` passed the decimal parser and could trigger enormous exact-rational arithmetic. | Bound decimal exponents before arithmetic; preserve existing sub-float risk-budget precision. |
| W03 / W04 | Changing authorization left reports, review controls or an in-flight research result associated with the previous identity. | Clear results and abort/invalidate pending requests when authorization changes. |
| W04 | A delayed MFA enrollment/login response could expose the previous identity's setup seed or overwrite the replacement identity's authorization. | Bind authentication responses to their request generation; clear transient state and abort on identity change/unmount. |
| W09 | NaN timing inputs produced a nominal clock report containing NaN. | Reject nonfinite and nonnumeric timing values before computing clock measurements. |
| W10 | A page of traces could starve alert delivery when only the alert destination was configured, and vice versa. | Filter the durable pending queue by configured destination before applying its batch limit. |
| W10 | OTLP HTTP 200 partial rejection was marked delivered; concurrent identical alerts could raise a version conflict. | Keep rejected traces pending with the same idempotency key; atomically deduplicate alert insertion. |
| W18 | Ready old pods could be mistaken for an updated deployment. Rollback PATCH acceptance was labeled recovered, and a failed first rollback prevented the second target's recovery. | Require updated/available/ready replica convergence; attempt both recoveries and verify prior image, replica count and readiness before reporting `rolled_back`. |

The change adds **37 backend regression cases and 4 frontend behavior cases**.
No broker request, deployment or external notification was made by these tests.

## Twenty-workstream implementation inspection

W01–W20 are the implementation IDs from
[PLATFORM20_IMPLEMENTATION.md](../PLATFORM20_IMPLEMENTATION.md), distinct from
release gates T01–T20. Existing implementations were reused.

| ID | Existing implementation inspected | Evidence exercised here | Still required for broader acceptance |
|---|---|---|---|
| W01 | `control_plane/vendors.py`, provider integration | Vendor response/rights/pagination fixtures and API suite | Provider credentials, source-owner rights and genuine capture validation |
| W02 | `control_plane/broker.py`, canonical paper OMS | Risk, intent binding, partial/full/terminal snapshots, retry/reconciliation and decimal regressions | Authorized broker-paper capture, operational reconciliation and full FIX session implementation |
| W03 | `ResearchWorkspace.tsx`, Admin Centre | Frontend suite, type check/build and Chromium research flow | Backend-backed chart/options/news coverage for the remaining labeled demo panels |
| W04 | `identity.py`, `SecuritySetup.tsx` | TOTP/session/RBAC tests, identity-change regressions and Chromium MFA | Independent IAM/security review and account-recovery policy |
| W05 | `secrets.py` | Encryption/AAD/protected-key tests | External key custody and deployed Vault acceptance |
| W06 | `coordination.py` | Single-authority lease, concurrency and fencing tests | Independent-host HA, split-brain/failover acceptance; no quorum consensus is implemented |
| W07 | `replication.py` | Signed ordering, gap/conflict rejection, atomic rollback and event-set convergence | Independent-node reconciliation and financial-state consensus |
| W08 | Rust `peer_metrics.rs`, `peer_telemetry.py` | Rust tests and three local E2EE node processes with verified receipt metrics | Sustained independent-host telemetry/provenance and NAT/relay/open-mesh acceptance |
| W09 | `clock.py` | Actual loopback SNTP exchange, nonce checks and invalid-time regressions | Authenticated clock source/NTS and independent-host drift evidence |
| W10 | `observability.py` | Metrics, outbox, exporter rejection/starvation and concurrency tests | Real configured exporter acceptance, alert operations, SLO/capacity review |
| W11 | `features.py`, lineage helpers | Point-in-time feature/backfill and lineage tests | Independently verified datasets and distributed feature serving |
| W12 | `models.py`, `experiments.py` | Actual fitting/holdout, immutable experiment/reproduction and drift tests | Frozen genuine dataset, blinded OOS/leakage review and independent model/risk approval |
| W13 | `shadow.py` | Lagged persistent shadow runs, costs and risk-stop tests | Calibrated multi-asset fill/borrow/margin models and broker-paper comparison |
| W14 | `agents.py` | Tool allowlist, separate approver and actual restricted subprocess | Full OS isolation, independent runtime review; autonomous model-driven planning is not implemented |
| W15 | `coordination.py`, `cli.py` | Lease/retry/stale-worker tests and a separate worker over verified HTTPS | Independent-machine scheduling and failure acceptance |
| W16 | `accounting.py`, `paper_execution.py` | Canonical fills, journal/projection reconciliation, strategy attribution and market-to-paper exercise | Independent broker reconciliation and unrealized multi-asset attribution |
| W17 | `depth.py` | Snapshot sequence/replay/freshness, partial depth and impact fixtures | Licensed genuine streaming/depth acceptance across venues |
| W18 | `deployment.py` | Canary/rollback fixtures and convergence/recovery regressions | Actual authorized staging rollout/rollback and independent-host DR drill |
| W19 | `custody.py`, existing evidence validators | Ed25519/Object Lock fixtures, custody and intake validation | Actual independently held signed evidence and S3 retention/readback acceptance |
| W20 | `crates/native-feed`, `native.py` | Rust queue/view/thread tests and actual capture process tests | Target-hardware benchmark, numerical parity and live-feed acceptance |

The additional planning backlog in #413–#416 is broader than these twenty local
implementations. In particular, privacy-preserving federated/distributed model
training, tenant isolation, marketplace/billing, jurisdiction-specific compliance
and fully calibrated execution simulation are not completed by the existing
research primitives or by this hardening change.

## Reproduce validation

Use a project virtual environment with `services/quant-api[dev]`, PyYAML, Node 24,
the committed npm lockfile, Chromium and a Rust toolchain. No external service
credentials are needed for the following local checks:

```bash
python -m pytest -q services/quant-api/tests tests/test_ultra_fast_feed.py
cd apps/web
npm test
npm run build
cd ../..
cargo test --manifest-path crates/risk-kernel/Cargo.toml
cargo test --locked --manifest-path services/p2p-node/Cargo.toml --all-targets
cargo test --locked --manifest-path crates/native-feed/Cargo.toml
cargo build --locked --manifest-path crates/native-feed/Cargo.toml
python scripts/test_native_feed_process.py
OSIRIS_P2P_TEST_BINARY="$PWD/services/p2p-node/target/debug/osiris-p2p-node" \
  python scripts/test_p2p_delivery_receipts.py
OSIRIS_TEST_PYTHON="$(command -v python)" bash scripts/run_platform20_browser.sh
python scripts/run_market_paper_acceptance.py --output /tmp/workstream-paper.json
python scripts/check_release_gate_status.py
python scripts/check_release_gate_status.py --strict # expected failure: 17 open gates
```

Local source-bound results are recorded alongside this document in
`WORKSTREAM_AUDIT_2026_11_12.json`. They are engineering test results, not an
independent release decision. The JSON source hashes allow later source changes
to be distinguished from the tested worktree.

## Outstanding release dependencies

- T06/T20: independent cryptography, model/risk and operator decisions, including
  the actual signed findings/retest and paper-only release signoff.
- T07–T10: source-owner entitlements, provider configuration and independently
  checked streaming/corporate-action/macro provenance.
- T04/T18/T19: authorized independent hosts, sustained NAT/relay/open-mesh load,
  network failure injection and actual backup/restore/failover with measured
  RTO/RPO and retained artifacts.
- T11–T17: reviewed candidate datasets/models, realistic costs and independent
  end-to-end execution/monitoring acceptance on the final candidate.
- T01: final-candidate supply-chain evidence and resolution/review of the tracked
  residual maintenance advisory. Green tests are not a new dependency audit.

`live_trading_authorized=false`. Release gates and open issues remain pending
where their evidence is missing. Credentials belong in secure environment
settings; no secrets are requested or stored in this audit.
