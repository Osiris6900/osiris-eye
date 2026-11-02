# Shared data and API integration

The web dashboard reads the shared module catalog and health endpoint through the Vite `/api` proxy. The FastAPI service is the common entry point for research modules.

## Local development

Terminal 1 — API:

```bash
cd services/quant-api
pip install -e '.[dev]'
# Optional: choose a durable local database location
export OSIRIS_DB_PATH=../../data/osiris_research.sqlite3
uvicorn osiris_api.main:app --reload
```

Terminal 2 — dashboard:

```bash
cd apps/web
npm install
# Optional when API is not on localhost:8000
OSIRIS_API_TARGET=http://127.0.0.1:8000 npm run dev
```

The Vite development proxy maps browser requests from `/api/*` to the API root.

## Common endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | API health, mode and module count |
| `GET /platform/modules` | Central module catalog |
| `POST /data/observations` | Upsert a point-in-time observation |\n| `POST /data/audit` | Audit batch quality, freshness, duplicates and timestamp ordering |\n| `POST /research/backtest/simulate` | Lagged signal/return simulation with turnover costs |
| `GET /data/observations?dataset=...` | Read latest observations for a dataset |
| `POST /research/microstructure/imbalance` | Normalized displayed-volume imbalance |
| `POST /research/portfolio/weights` | Inverse-volatility baseline weights |
| `POST /research/risk/historical` | Historical VaR and expected shortfall |
| `POST /research/models/drift` | Population stability index |
| `POST /research/strategy/summary` | Return, Sharpe and drawdown diagnostics |
| `POST /research/execution/cost` | Estimated spread/slippage/impact cost |
| `POST /research/macro/regime` | Transparent macro/volatility heuristic |
| `POST /risk/check` | Fail-closed simulated pre-trade risk check |

Interactive API documentation is available at `/docs` while the local API is running.

## Observation contract

Each observation carries:
- `dataset`, `record_key`, and `source` for stable identity and lineage
- `event_time`: when the underlying event occurred
- `available_at`: when the information became available to the strategy
- `payload`: a JSON object of normalized fields

Timestamps must include a timezone and are normalized to UTC. Re-sending the same dataset/source/record key updates the row, making ingestion idempotent. The API does not silently infer availability from event time.

## Limitations

SQLite is for local development and early integration, not concurrent institutional production workloads. No licensed market-data adapters, raw immutable object store, vendor entitlement checks, broker connections, or live execution are included. Historical research must join on `available_at` to avoid look-ahead bias. Do not put secrets or personal data in observation payloads.


## New research foundations

The data audit endpoint evaluates caller-supplied records at an explicit `as_of` timestamp. It reports missing fields, malformed or timezone-naive timestamps, duplicate identities, future availability, event/availability ordering and stale observations. Any blocking issue or stale record sets `new_risk_allowed=false`. This is a quality gate over supplied records, not a live feed monitor or vendor connector.

The backtest simulator applies `signals[t-1]` to `asset_returns[t]`, subtracts a configurable turnover cost, and reports period returns, cumulative return, drawdown and turnover. It is intentionally a minimal research prototype: it does not model orders, fills, spread dynamics, market impact, borrow, funding, corporate actions, contract rolls, exchange calendars or capacity. Do not treat its output as a validated strategy result.
