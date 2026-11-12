# Twenty-workstream implementation

This candidate adds executable research and operations services for all twenty areas in the November 11 audit. The delivery scope is a **local MVP**, with explicit configuration boundaries. It does not accept any outstanding release gate or enable live trading. Existing PRs #351, #352, #353, #385, #401, #402, #403, #404, #406 and #407 are included in this integration branch; their original branches are preserved.

The workstream IDs below are separate from release gates T01–T20.

| ID | Area | Implemented behavior | Remaining boundary |
|---|---|---|---|
| W01 | Vendor adapters | Alpaca equity daily bars, OANDA practice candles, Polygon aggregates and Twelve Data volume-bearing bars; bounded pagination, schema validation and conservative retrieval-time availability. | Credentials, actual vendor connectivity and rights approval. Coverage is narrower than each vendor's full product catalog. |
| W02 | Broker/FIX | Opt-in Alpaca **paper** limit-order transport, risk checks, persisted client IDs, uncertain-outcome reconciliation, cancellation and FIX 4.4 checksum/body-length codec. | Live broker activation remains deferred by the user. A live connector, full FIX session and broker operational acceptance are not completed. |
| W03 | Dedicated screens | Authenticated canonical account/risk/feed/network/signal views, source-labelled price charts, immutable source-governed option/news snapshots, descriptive regime diagnostics, reviewed Strategy Lab, backend backtesting and persisted reports. | Genuine source configuration/rights, independent model validation, final CI and review. Synthetic fixtures remain explicitly labelled. |
| W04 | MFA/sessions | TOTP enrollment, RFC 6238 verification, replay rejection, persistent lockout, 15-minute opaque sessions and revocation. | Uses existing configured role-token identities; no new password directory or account-recovery authority. |
| W05 | Secrets | AES-GCM encrypted local credentials, associated-data binding and HTTPS Vault KV-v2 reads. API never returns stored secret values. | External master-key protection, Vault deployment and independent review. |
| W06 | HA/fencing | Atomic authority leases, monotonically increasing fencing epochs and rejection of expired/foreign writers. | Single SQLite authority; not Raft/quorum, independent-host failover or a production HA certification. |
| W07 | Replication | Pinned Ed25519 origin keys, ordered event batches, duplicate handling, conflict/gap rejection and deterministic event-set convergence. | Financial account-state consensus and independently hosted replication remain unproved. |
| W08 | Peer telemetry | Actual admitted Noise connections and verified admission receipts update bounded counters and latency histories exposed at `/peer-metrics`; Python persists collected snapshots. | Receipt latency is not transport ping RTT. Local endpoint provenance is operator-controlled, not an independent host attestation. |
| W09 | Clock monitoring | Bounded SNTP probe, originate-nonce validation, offset/delay measurement and wall-clock-jump rejection. | No NTS authentication, PTP implementation or OS clock adjustment. |
| W10 | Observability | Persistent counters, Prometheus exposition, bounded OTLP/HTTP trace export batches and deduplicated alert-webhook outbox. | Export destinations require explicit operator configuration. Delivery is at least once; production retention/capacity review remains. |
| W11 | Feature store | Immutable feature versions, event-time AND availability-time queries, source hashes and inference through the stored training transform. | Distributed feature serving and external time/source attestation. |
| W12 | Model pipeline | Actual ridge-model fitting, chronological holdout, baseline comparison, persisted transforms/artifact hashes and signed/versioned paper-research alias promotion. | This baseline is not an approved trading model or independent OOS validation. |
| W13 | Shadow trading | Persistent isolated long/flat runs, previous-tick signal alignment, fees, cash/risk limits and aligned paper-equity comparison. | No live broker effects or complete multi-asset execution model. |
| W14 | Research-agent runtime | Role-allowlisted tools, separate approver, review queue and resource-limited subprocess execution. Shell/code/URL tools are not accepted. | No autonomous LLM planning; subprocess resource limits are not a full OS security sandbox. |
| W15 | Worker execution | Leased durable jobs, retry ceilings, stale-claim rejection and workers that claim/finish through authenticated HTTPS. | Local processes were tested, not independent machines or production scheduling availability. |
| W16 | Attribution | Reads actual canonical SQLite fills, verifies audit/reconciliation and groups realized P&L, fees and reference-price slippage by immutable strategy tags. | Unrealized attribution is not included. Slippage is diagnostic, not an additional P&L deduction. |
| W17 | Depth analytics | Read-only Coinbase L2 snapshot capture, sequence/replay checks, persistence, freshness checks and depth-limited impact calculation. | No incremental multi-venue depth stream; provider terms and real feed acceptance remain external. |
| W18 | Rollout/rollback | Signed research-only release manifests, immutable image digests, distinct research/canary targets, readiness checks and rollback requests. | Kubernetes behavior was tested with fixtures; no production deployment was performed. |
| W19 | Evidence custody | Ed25519 signing/verification, conditional S3 writes, versioned Object Lock COMPLIANCE requests and retention readback. | No actual S3 write or independent signing-key custody attestation. Optional infrastructure SDK installation requires its own deployment review. |
| W20 | Native feed | Rust SPSC queues, borrowed binary views, immutable-file memory mapping, bounded prefetch, real worker threads and multipath sequence deduplication. Python pins the executable digest and capture root. | Offline captures only; external file mutation is unsupported. No target-hardware latency/throughput claim or live-routing permission. |

## Runtime configuration

The API does not initialize the control-plane database or start an external integration on import. New routes are under `/control-plane/`, separate from legacy `/platform/modules` and its existing API-token middleware.

- `OSIRIS_CONTROL_DB`: operator-owned persistent SQLite path; files are created with mode 0600.
- `OSIRIS_CONTROL_MASTER_KEY`: base64-encoded 32-byte key, supplied through secure environment settings, not committed or posted in chat. Used for local credential encryption.
- `OSIRIS_ADMIN_TOKENS`: existing per-role bootstrap token configuration. Read access respects role scopes. Mutating research/operations APIs require an MFA `Session` token.
- `OSIRIS_ENV=production`: MFA and credential APIs require HTTPS even on loopback.
- Vendor bars require `OSIRIS_VENDOR_ADAPTERS_ENABLED=1`, provider credentials and `OSIRIS_<PROVIDER>_ENTITLEMENT_REFERENCE`. These references are operator claims, not independent licensing proof. No adapter is enabled by default.
- Depth capture additionally requires `OSIRIS_L2_ENTITLEMENT_REFERENCE`.
- `OSIRIS_REPLICATION_PUBLIC_KEYS`: JSON map of configured origin IDs to base64 Ed25519 public keys. Unknown origins, conflicting IDs and sequence gaps fail closed.
- `OSIRIS_NTP_HOST`: explicitly selected clock source. Clock measurement does not establish source authentication.
- OTLP and alerts require `OSIRIS_OTLP_TRACES_URL` / `OSIRIS_ALERT_WEBHOOK_URL` and an explicit exporter invocation. No notification was sent during development.

Sessions and authenticator seeds are never saved in browser storage. HTTP auth responses use `Cache-Control: no-store`. The encrypted secret-write API cannot overwrite protected TOTP enrollment keys. Logical role identities stay stable across primary-token rotation, preserving MFA enrollment and the self-review prohibition. The role configuration digest invalidates existing sessions after role-token configuration changes.

## Worker and operator commands

Install the API development dependencies in a project virtual environment. All commands below are opt-in:

```bash
# Local worker over the configured authority database; executes reviewed queued work.
python -m osiris_api.control_plane.cli worker --database /path/to/control.sqlite3 --once

# Remote worker: HTTPS endpoint + session reference are explicit environment settings.
OSIRIS_PLATFORM_API_URL=https://research.example \
OSIRIS_WORKER_SESSION_REF=env:OSIRIS_WORKER_SESSION \
python -m osiris_api.control_plane.cli worker --database /path/to/worker.sqlite3

python -m osiris_api.control_plane.cli collect-peers --database /path/to/control.sqlite3
python -m osiris_api.control_plane.cli clock --database /path/to/control.sqlite3
```

`export-telemetry`, `replicate`, `retain-evidence` and `rollout` are separate operator commands. S3 and rollout require explicit enable flags and configured resources. Rollout accepts only signed `research-only` manifests and `osiris-research-*` deployment targets; it cannot enable trading. The normal deployment lock excludes the optional `infrastructure` SDK extra.

## Reproducible validation

The source-bound local engineering profile covers W01–W20 without relaxing the
default strict release profile:

```bash
python scripts/verify_all.py --profile engineering # read-only plan
python scripts/verify_all.py --profile engineering --execute --report /tmp/osiris-engineering.json
```

It runs backend/frontend/Rust/native-process/peer-receipt/browser checks and real
temporary paper backup/restore, retains hashed local artifacts, and rejects
source changes during verification. Its report maps every workstream to checks.
`engineering_checks_passed` does not mean independent acceptance or production
completion. `--profile release` remains the default and retains strict gates.
See `docs/workstreams/WORKSTREAM_DELIVERY_2026_11_12.md` for source and external
acceptance boundaries.

```bash
python -m pytest -q services/quant-api/tests tests/test_ultra_fast_feed.py
python -m unittest discover -s scripts -p 'test_check_p2p_workflows.py'
cargo test --locked --manifest-path services/p2p-node/Cargo.toml --all-targets
cargo test --locked --manifest-path crates/native-feed/Cargo.toml
cargo build --locked --manifest-path crates/native-feed/Cargo.toml
python scripts/test_native_feed_process.py
OSIRIS_P2P_TEST_BINARY=/path/to/built/osiris-p2p-node python scripts/test_p2p_delivery_receipts.py
cd apps/web && npm test && npm run build
```

For the real browser flow, install Chromium or Google Chrome and Node 24, then run `bash scripts/run_platform20_browser.sh` from the repository root. `OSIRIS_TEST_PYTHON` may select the project virtual-environment interpreter. The harness creates disposable identities and a private test CA, verifies TLS without disabling certificate checks, runs two role identities through MFA/review and a separate HTTPS worker, and cleans up its own processes and private fixture files. Only a sanitized screenshot and result JSON are retained. CI runs this harness and the native process tests.

Verified locally during implementation: 1,466 Python tests passed, one existing test skipped; 50 frontend tests, TypeScript/build, Rust all-target tests, native FIFO/thread/view and process tests, real three-node receipt telemetry, and the browser MFA → research request → separate approver → HTTPS worker → completed report flow passed. Dependency matching found no vulnerability groups in the 607-package existing graph and no matches in the 13-package native-feed graph; the existing `paste` maintenance advisory remains. Fixture tests of Vault/S3/Kubernetes do not claim that those external services were contacted.

Release acceptance remains **3 evidenced / 14 partial / 3 remaining**, with live execution unauthorized. Independent security/model-risk review, feed rights, multi-host acceptance and operational release decisions remain separate deliverables.
