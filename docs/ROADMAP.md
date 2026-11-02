# Roadmap

## Phase 0 — Foundation
Python API and six research diagnostics; Rust risk kernel; TypeScript web app; tests and CI. Existing dashboard preview retained.

## Phase 1 — Data integrity
Instrument master, authorized vendor adapters, immutable raw data, normalized time series, freshness/gap/corporate-action checks.

## Phase 2 — Research and backtesting
Event-driven simulator with fees, spread, slippage, market impact, borrow/funding and exchange calendars; walk-forward and out-of-sample validation.

## Phase 3 — Paper trading
Sandbox adapter after controls pass review; reconciliation, idempotency, order-state machine, kill switch, alerting and daily loss limits.

## Phase 4 — Production review
Independent security, operational, model-risk and compliance review; disaster recovery, monitoring, capacity tests, broker/exchange permissions and applicable regulation. Live execution remains out of scope until all gates pass.