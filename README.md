# OSIRIS EYE — Institutional Quant Platform

OSIRIS EYE is a research-first, multi-asset quantitative trading platform scaffold. The existing dashboard (`index.html`) is preserved. This repository adds a Python research/API layer, a Rust risk kernel, a TypeScript web app foundation, tests, and CI.

> **Safety status:** research and paper-trading scaffold only. No live broker connection, exchange credentials, or order-routing is enabled. Market examples are synthetic. Do not use for live trading without independent validation, security review, broker/exchange integration, and applicable regulatory review.

## Architecture

- `services/quant-api`: FastAPI service, shared SQLite point-in-time observation store, 107-module registry, research endpoints, risk checks, and tests.
- `crates/risk-kernel`: Rust pre-trade risk primitives.
- `apps/web`: TypeScript + Vite dashboard foundation.
- `docs`: architecture, data contracts, shared API integration guide, module catalog, advanced intelligence specification, master engineering blueprint, and phased roadmap.
- `.github/workflows/ci.yml`: Python, Rust, and TypeScript CI.

## Platform module catalog

The platform now registers 107 research and infrastructure capability foundations; registry status indicates implementation maturity, not production readiness. Deterministic Python primitives now cover alternative-event validation, order-flow imbalance, baseline portfolio weights, historical VaR/ES, model drift, strategy diagnostics, execution-cost estimation, macro regime labels, component health, and approval-gated AI task contracts. These are not complete services or validated trading signals.

## Market Mechanics research lab

Ten additional diagnostics cover dealer Gamma Exposure (GEX), options expiry and Max Pain, liquidity vacuums, aggressor order-flow imbalance, dark-pool activity, crypto funding and liquidation scenarios, Treasury liquidity and repo spreads, index rebalancing, volatility-control fund flows, and cross-asset lead–lag. The dashboard's Market Mechanics panel loads editable synthetic examples from `/research/market-mechanics/catalog` and runs the selected calculation through the API.

Each module exposes its evidence strength, assumptions and limitations. Strong mechanical support does not establish a profitable prediction: GEX needs explicit signed dealer positions, Max Pain has weak predictive support, dark-pool volume does not reveal institutional direction, and lead–lag relationships can be unstable. These calculations use caller-supplied data and add no vendor feeds or order routing. See [Market Mechanics documentation](docs/MARKET_MECHANICS.md) for endpoints, formulas, units and usage.

## Phase 7 institutional alpha & market intelligence

Six prototype research systems add cross-asset correlation/lead-lag hypothesis scans, options-flow premium aggregation, heuristic liquidity forecasts, robust market-anomaly alerts, offline reinforcement-learning policy evaluation, and trade-surveillance review indicators. These are not live data integrations, calibrated forecasts, autonomous policy training, or misconduct determinations. See [Phase 7 documentation](docs/PHASE7_INSTITUTIONAL_ALPHA_MARKET_INTELLIGENCE.md).

## Phase 6 advanced quant intelligence

Six prototype research APIs add chronological walk-forward fold diagnostics, weighted portfolio risk and concentration, participation/square-root execution-cost heuristics, feature PSI plus accuracy-drift checks, rule-based macro regime labels, and strategy triage with turnover and multiple-testing penalties. These are transparent research primitives: no external macro feed, calibrated market-impact model, model deployment authorization, or live order routing is provided. See [docs/PHASE6_ADVANCED_QUANT_INTELLIGENCE.md](docs/PHASE6_ADVANCED_QUANT_INTELLIGENCE.md).

## Phase 5 institutional backtesting\n\nA multi-asset portfolio simulator applies target weights with a one-period lag and models explicit commission, spread, slippage and impact assumptions. It reports equity, CAGR, volatility, Sharpe and drawdown diagnostics. See [docs/PHASE5_INSTITUTIONAL_BACKTESTING.md](docs/PHASE5_INSTITUTIONAL_BACKTESTING.md). This remains a research simulator, not fill-level execution.\n\n## Phase 4 market-data infrastructure\n\nSix foundations add batch validation and ingestion, UTC normalization, instrument-master audits, feed freshness checks, operator-declared entitlement gating, and event-time plus availability-time point-in-time queries. See [docs/PHASE4_MARKET_DATA_INFRASTRUCTURE.md](docs/PHASE4_MARKET_DATA_INFRASTRUCTURE.md). These use local SQLite; no vendor feeds or production streaming service are connected.\n\n## Advanced intelligence systems

The seven requested capability groups are now specified in [docs/ADVANCED_INTELLIGENCE_SYSTEMS.md](docs/ADVANCED_INTELLIGENCE_SYSTEMS.md):
1. Multi-Agent AI research team with scoped permissions and human approval.
2. Probabilistic market-prediction ensemble with walk-forward validation and abstention.
3. Explainable anomaly detection for markets, options, liquidity, data quality and model drift.
4. Global multi-asset intelligence network with instrument masters, exchange calendars and UTC/source timestamps.
5. Advanced quantitative research lab with reproducible experiments and bias controls.
6. Weather, lunar, solar/space-weather and other alternative-data research lab.
7. Futuristic operator dashboard covering AI, global markets, options, anomalies, quant research and risk.

Data provenance, point-in-time joins, feed-quality gates, model records and security controls are defined in [docs/DATA_CONTRACTS_AND_VALIDATION.md](docs/DATA_CONTRACTS_AND_VALIDATION.md), and [docs/MASTER_ENGINEERING_BLUEPRINT.md](docs/MASTER_ENGINEERING_BLUEPRINT.md).\n\nThe broader target architecture—including deep order-flow intelligence, portfolio intelligence, smart execution research, market simulation, financial knowledge graph, observability, model governance, alternative intelligence, latency discipline, security and phased acceptance gates—is documented in [docs/MASTER_ENGINEERING_BLUEPRINT.md](docs/MASTER_ENGINEERING_BLUEPRINT.md).

These documents specify the target architecture and acceptance criteria; they do not mean all live feeds, trained models, agent runtime, or dashboard screens are already implemented.

## Quick start

### Python API

```bash
cd services/quant-api
python -m venv .venv
# Windows: .venv\\Scripts\\activate  |  macOS/Linux: source .venv/bin/activate
pip install -e '.[dev]'
uvicorn osiris_api.main:app --reload
```

### Rust risk kernel

```bash
cd crates/risk-kernel
cargo test
```

### TypeScript web app

```bash
cd apps/web
npm install
npm run dev
```

## Shared data and API

The dashboard reads `/health` and `/platform/modules` through the Vite API proxy. Research modules share one FastAPI service and a local SQLite observation store. See [docs/DATA_API_INTEGRATION.md](docs/DATA_API_INTEGRATION.md) for setup, endpoints, timestamp rules, and limitations.

## Current research features

Options IV peer z-score, liquidity cost estimate, expiry/gamma exposure feature, implied-vs-forecast volatility spread, cross-market lagged correlation diagnostic, and a simple volatility/spread regime flag. These are transparent diagnostics, not profitable strategies or validated forecasts.

## Engineering rules

1. Synthetic data in tests; licensed/authorized feeds only in adapters.
2. Backtests must model fees, spread, slippage, market impact, corporate actions, and survivorship bias.
3. Use walk-forward and out-of-sample validation; never tune on the final test set.
4. Keep credentials in a secret manager or environment variables; never commit secrets.
5. Paper trading and kill-switch validation before any live execution work.
6. Every order path must enforce risk limits independently of the strategy model.
7. Agents and strategy models cannot bypass the independent risk gate.
8. Stale or invalid critical data must fail closed and block new simulated risk.

See [docs/ROADMAP.md](docs/ROADMAP.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/PLATFORM_MODULES.md](docs/PLATFORM_MODULES.md), [docs/ADVANCED_INTELLIGENCE_SYSTEMS.md](docs/ADVANCED_INTELLIGENCE_SYSTEMS.md), and [docs/DATA_CONTRACTS_AND_VALIDATION.md](docs/DATA_CONTRACTS_AND_VALIDATION.md).


### Phase 9 — Global Multi-Asset Intelligence

Eight research/control prototypes extend the platform: global instrument coverage, counterparty concentration, corporate-action and tax-lot audit, portfolio capacity, derivatives Greeks aggregation, financial knowledge-graph integrity, disaster-recovery RTO/RPO checks, and benchmark-relative performance. See [Phase 9 documentation](docs/PHASE9_GLOBAL_MULTI_ASSET_INTELLIGENCE.md). These are caller-data diagnostics, not connected feeds, tax advice, calibrated risk models, production failover, or live execution.


### Phase 10 — Institutional Intelligence & Capital Protection

Eight additional prototype systems cover portfolio risk monitoring, fail-closed vendor selection, alpha experiment ranking, risk-budget allocation, AI-agent governance, execution TCA, systemic stress scenarios, and independent strategy approval gates. See [Phase 10 documentation](docs/PHASE10_INSTITUTIONAL_INTELLIGENCE.md). These are caller-supplied-data diagnostics and do not enable live execution or deployment.


## Phase 15 — Tamper-evident audit evidence

Deterministic SHA-256 hash-chain construction and verification now detect edits, reordering, missing links, and inconsistent chain heads in caller-supplied audit evidence. This is not durable immutable storage, actor authentication, a digital signature system, or proof of event truth. See [Phase 15 documentation](docs/PHASE15_TAMPER_EVIDENT_AUDIT.md).


## Phase 16 — Durable SQLite audit journal

Audit events can now be appended transactionally to the configured SQLite database and verified as a persistent hash chain. Duplicate event IDs are rejected and row tampering is detected. This is not WORM storage or protection against a privileged database rewrite; see [Phase 16 documentation](docs/PHASE16_DURABLE_AUDIT_JOURNAL.md).


## Phase 17 — External audit checkpoints

Operator-held SHA-256 checkpoints record a durable journal sequence and head hash, allowing later verification to detect missing/truncated history and checkpoint mismatches. Copy checkpoints to independently controlled storage; a checkpoint kept only beside SQLite is not an external trust anchor. This is not a signature, trusted timestamp, WORM guarantee, or proof of event truth. See [Phase 17 documentation](docs/PHASE17_EXTERNAL_AUDIT_CHECKPOINTS.md).


## Phase 18 — Portable audit evidence bundles

Phase 18 adds offline-verifiable JSON exports of the durable audit journal,
optionally including a Phase 17 checkpoint. Verification checks bundle digest,
event sequence/hash links, unique event IDs, head hash, and checkpoint mapping.
The bundle is not signed and does not provide trusted timestamps, independent
custody, WORM retention, or proof of source-event truth. See
[Phase 18 documentation](docs/PHASE18_PORTABLE_AUDIT_BUNDLES.md).


## OSIRIS P2P observation network

The Rust node exchanges signed observations over authenticated libp2p connections. Recipient E2EE is opt-in (`OSIRIS_E2EE_ENABLED=1`); durable key-epoch storage, replay protection and authority-signed revocation support its key lifecycle. The hardening work adds explicit Circuit Relay v2 support to the core node, bounded connection admission, strict observation timestamps and optional freshness checks, plus coordinated cross-host acceptance helpers.

The first release targets encrypted market-data research and paper trading. There is no automatic observation-to-paper-order adapter or live order routing. Public-node deployment, live peer count, cross-host E2EE acceptance and independent security assurance require their own evidence; implementation alone does not establish them.

See the [release gates and validation commands](docs/P2P_RELEASE_GATES.md), [security model](docs/P2P_SECURITY_MODEL.md), [E2EE configuration](docs/P2P_LIVE_E2EE_ROLLOUT.md), [hardened receiver profile](docs/P2P_HARDENED_DEPLOYMENT.md) and [public-node setup](docs/P2P_PUBLIC_NODE_DEPLOYMENT.md). The deployment files are operator preparation; no service or public endpoint is started by this repository.

## License

OSIRIS EYE is free software licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0). You may use, study, modify and redistribute it under those terms; operators offering a modified version over a network must offer its source to users (Section 13).
