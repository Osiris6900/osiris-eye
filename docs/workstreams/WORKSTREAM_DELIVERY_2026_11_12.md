# Current W01–W20 engineering delivery

This follows the existing integration #408 and dashboard #424. The local
implementations are testable together; independent acceptance is a separate
decision. No checkbox or release gate is promoted by this document.

## Source-governed market views

`control_plane/market_views.py` reuses the configured control database and
existing decimal, canonical hashing and market-bar normalization code.

- `POST /control-plane/market/views`: an operations-scoped MFA session imports
  one option-chain or news record. Sources must be explicitly allowlisted by
  `OSIRIS_MARKET_VIEW_SOURCES`; scope, expiry and rights reference are mandatory.
  There is no arbitrary URL fetch, automatic subscription or broker call.
- `GET /control-plane/market/views/options?entity=NIFTY` and
  `/control-plane/market/views/news?entity=GLOBAL`: research-read permission is
  required. Queries enforce both event time and server availability time,
  freshness, present source permissions and content hashes. Revocation or a
  changed rights reference/mode hides old records. Identical imports deduplicate;
  a conflicting existing ID is rejected. Actual intake time is never backdated
  to a vendor's historical availability claim.
- `GET /control-plane/market/regime`: the same authenticated, allowlisted bar
  gateway supplies at least 21 ordered observations. Versioned descriptive
  log-return thresholds report trend, range, volatility or drawdown/crisis,
  source/mode, interval endpoints and the bars digest. Volatility is explicitly
  per observation. No liquidity observation, annualized volatility, OOS/model
  approval, profitable forecast or live execution is inferred.
- Option quotes validate expiry, currency, positive strikes, uncrossed bid/ask,
  distinct contracts and nonnegative integer counters. News validates HTTPS
  source links and bounded optional source-supplied sentiment. UI records show
  source, synthetic/operator-declared mode, times and rights-review limits.
- `control_plane.client.ResearchClient` is an importable synchronous SDK for
  dashboard/reports, source intake/reads and history/regime. Authorization stays
  in headers, origins must use HTTPS, and ambiguous writes are never retried.

The source configuration maps a source ID to these exact fields:

```json
{
  "offline-example": {
    "mode": "synthetic",
    "rights_reference": "disposable-fixture-only",
    "expires_at": "2026-11-13T00:00:00Z",
    "kinds": ["options", "news"],
    "entities": ["NIFTY", "GLOBAL"],
    "max_age_seconds": 300
  }
}
```

Real source owners must supply their own permitted entities/uses, expiry and
rights reference; `operator_licensed` is an operator declaration, never verified
vendor countersignature or T09 acceptance. No production source is enabled by
default. Tokens and keys belong in secure environment settings.

An input record contains exactly `id`, `source`, `kind`, `entity`, `event_time`,
`available_at`, `payload`. News payloads contain `title`, `url`, `symbols`,
`sentiment` (number from -1 to 1 or null). Option payloads contain `underlying`,
`expiry` (YYYY-MM-DD), `currency`, and `contracts`; each contract contains
`symbol`, `option_type` (CALL/PUT), decimal-string `strike`/`bid`/`ask`, and integer
`volume`/`open_interest`. Local OpenAPI describes authenticated routes.

```python
import os
from osiris_api.control_plane.client import ResearchClient

client = ResearchClient(os.environ["OSIRIS_PLATFORM_API_URL"],
                        os.environ["OSIRIS_CLIENT_AUTHORIZATION"])
snapshot = client.market_views("options", "NIFTY")
# An operations MFA session may explicitly import a permitted source record:
# receipt = client.ingest_market_view(record)
```

## All twenty implementation workstreams

The engineering profile records named checks for every row. Shared backend
tests exercise the existing implementations; table membership is not a claim
that a fixture proves production acceptance.

| ID | Implementation exercised | External or broader acceptance still required |
|---|---|---|
| W01 | Vendor normalization, pagination, source-policy and entitlement-intake regressions | Vendor credentials, genuine captures, original contract/rights verification |
| W02 | Canonical paper OMS, exact risk, intent/ack binding, fill/reconcile, Rust risk vectors | Authorized paper-broker operational acceptance; full FIX session remains broader work |
| W03 | Data-backed views, source intake, SDK, frontend and actual Chromium navigation | Genuine source connections and independent UI/data acceptance |
| W04 | TOTP/session rotation/revocation, role gates and browser MFA | Independent IAM review and owner account-recovery policy |
| W05 | AES-GCM/AAD secrets, protected TOTP keys and failed-secret-read regressions | External master-key custody and deployed Vault acceptance |
| W06 | Authority lease/fencing/retry/concurrency tests | Independent-host failover and partition evidence; quorum consensus is not implemented |
| W07 | Signed append-only replication, gaps/conflicts and event-set convergence | Independent-node operational evidence; financial-state consensus is not implemented |
| W08 | Actual Rust peer counters and three local E2EE processes with verified receipts | Sustained independent-host NAT/relay/open-mesh and attested telemetry |
| W09 | Real loopback SNTP/nonce/jump tests | Authenticated time source/NTS and independent-host drift evidence |
| W10 | Persistent metrics, OTLP partial rejection, queue fairness and deduplicated alerts | Configured exporter delivery, retention, SLO and alert operations acceptance |
| W11 | Immutable point-in-time features and backfill/source lineage | Genuine verified datasets and distributed serving |
| W12 | Fitted chronological holdout, immutable experiment reproduction, synthetic walk-forward archive | Frozen genuine data, blinded OOS/leakage/model/risk review |
| W13 | Lagged durable shadow runs, costs/stops and real temporary failure injection | Calibrated venue fill/borrow/margin/impact models and broker comparison |
| W14 | Allowlisted bounded subprocess tools and distinct research approver | Full OS isolation and independent runtime review; autonomous LLM planning is broader work |
| W15 | Durable job leases/retry/stale-claim rejection and separate verified-HTTPS worker | Independent-machine scheduler/failure acceptance |
| W16 | Canonical fill attribution, journal projection and market-to-paper accounting | Independent broker reconciliation and multi-asset unrealized attribution |
| W17 | Atomic source-bound L2 capture/history, durable clock/replay fencing, exact limit-aware depth and spread-cost estimates ([contract](DEPTH_IMPACT_HARDENING.md)) | Genuine licensed streaming/depth, venue/queue calibration and multi-venue acceptance |
| W18 | Rollout/rollback fixtures, real temporary SQLite backup/restore and continuation | Authorized real staging and independent-host service DR with measured RTO/RPO |
| W19 | Ed25519 custody/Object Lock fixtures and external-review intake rejection tests | Actual independently signed findings, S3 retention/readback and key custody |
| W20 | Real Rust FIFO/borrowed-view/thread/mmap tests and actual capture binary | Target-hardware benchmarks/parity and accepted live-feed integration |

## Verification and CI

```bash
python scripts/verify_all.py --profile engineering
python scripts/verify_all.py --profile engineering --execute --report /tmp/osiris-engineering.json
```

Use the project Python/Node/Rust toolchains on PATH. The executor runs the
existing backend/frontend/Rust suites, real native/P2P child processes, offline
market-to-paper flow, actual temporary backup/restore, portability and failure
tests, model archive, intake tests and Chromium/MFA/HTTPS-worker flow. Artifacts
are retained in a private temporary directory and individually hashed in the
report. A before/after source snapshot must match. Missing/failed checks never
become passes, and each workstream records its required check names. This is
local engineering evidence; `release_accepted`, `merge_authorized` and
`live_trading_authorized` stay false.

Core CI, dependency security and model-evidence workflows now accept PRs
targeting `codex/**` as well as main, retaining read-only token permissions and
immutable Action pins. Quant API and existing path-filtered checks retain their
existing triggers. A workflow regression test prevents stacked PRs from losing
this coverage. Final GitHub results and required independent review are still
necessary before integration/main acceptance.

`python scripts/verify_all.py --execute` keeps the original strict release
profile. It is expected to reject outstanding release gates; the engineering
profile never changes that rule or writes release-gate inventory.

## Scope and owner dependencies

The expanded planning backlog #413–#416 additionally includes tenant isolation,
privacy-preserving federated/distributed model training, strategy-marketplace
payments, billing, jurisdiction-specific compliance/tax and other product/R&D
work. Passing W01–W20 local checks does not complete those additional products.

Independent hosts, authorized vendor/broker access, original licences, external
security/model/risk reports, staging/DR targets and owner release decisions have
not been supplied to this environment. No substitute evidence is fabricated;
no release checkbox is closed. Live execution remains disabled.
