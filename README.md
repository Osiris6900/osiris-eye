# OSIRIS EYE — Institutional Quant Platform

OSIRIS EYE is a research-first, multi-asset quantitative trading platform scaffold. The existing dashboard (`index.html`) is preserved. This repository adds a Python research/API layer, a Rust risk kernel, a TypeScript web app foundation, tests, and CI.

> **Safety status:** research and paper-trading scaffold only. No live broker connection, exchange credentials, or order-routing is enabled. Market examples are synthetic. Do not use for live trading without independent validation, security review, broker/exchange integration, and applicable regulatory review.

## Architecture

- `services/quant-api`: FastAPI service, feature research modules, risk checks, and tests.
- `crates/risk-kernel`: Rust pre-trade risk primitives.
- `apps/web`: TypeScript + Vite dashboard foundation.
- `docs`: architecture, data contracts, and phased roadmap.
- `.github/workflows/ci.yml`: Python, Rust, and TypeScript CI.

## Ten platform modules

The ten-module foundation is documented in [docs/PLATFORM_MODULES.md](docs/PLATFORM_MODULES.md). Deterministic Python primitives now cover alternative-event validation, order-flow imbalance, baseline portfolio weights, historical VaR/ES, model drift, strategy diagnostics, execution-cost estimation, macro regime labels, component health, and approval-gated AI task contracts. These are not complete services or validated trading signals.

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

## Current research features

Options IV peer z-score, liquidity cost estimate, expiry/gamma exposure feature, implied-vs-forecast volatility spread, cross-market lagged correlation diagnostic, and a simple volatility/spread regime flag. These are transparent diagnostics, not profitable strategies or validated forecasts.

## Engineering rules

1. Synthetic data in tests; licensed/authorized feeds only in adapters.
2. Backtests must model fees, spread, slippage, market impact, corporate actions, and survivorship bias.
3. Use walk-forward and out-of-sample validation; never tune on the final test set.
4. Keep credentials in a secret manager or environment variables; never commit secrets.
5. Paper trading and kill-switch validation before any live execution work.
6. Every order path must enforce risk limits independently of the strategy model.

See [docs/ROADMAP.md](docs/ROADMAP.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), and [docs/PLATFORM_MODULES.md](docs/PLATFORM_MODULES.md).
