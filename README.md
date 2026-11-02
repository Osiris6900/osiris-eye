# OSIRIS EYE — Institutional Quant Platform

OSIRIS EYE is a research-first, multi-asset quantitative trading platform scaffold. The existing dashboard (`index.html`) is preserved. This repository adds a Python research/API layer, a Rust risk kernel, a TypeScript web app foundation, tests, and CI.

> **Safety status:** research and paper-trading scaffold only. No live broker connection, exchange credentials, or order-routing is enabled. Market examples are synthetic. Do not use for live trading without independent validation, security review, broker/exchange integration, and applicable regulatory review.

## Architecture

- `services/quant-api`: FastAPI service, shared SQLite point-in-time observation store, ten-module registry, research endpoints, risk checks, and tests.
- `crates/risk-kernel`: Rust pre-trade risk primitives.
- `apps/web`: TypeScript + Vite dashboard foundation.
- `docs`: architecture, data contracts, shared API integration guide, ten-module map, advanced intelligence specification, master engineering blueprint, and phased roadmap.
- `.github/workflows/ci.yml`: Python, Rust, and TypeScript CI.

## Ten platform modules

The ten-module foundation is documented in [docs/PLATFORM_MODULES.md](docs/PLATFORM_MODULES.md). Deterministic Python primitives now cover alternative-event validation, order-flow imbalance, baseline portfolio weights, historical VaR/ES, model drift, strategy diagnostics, execution-cost estimation, macro regime labels, component health, and approval-gated AI task contracts. These are not complete services or validated trading signals.

## Advanced intelligence systems

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
