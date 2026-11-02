# Architecture

Licensed market/news/macro/weather sources → ingestion adapters → immutable raw archive (Parquet/object storage) → normalized time-series store → point-in-time features → research/backtest engine → independent risk gate → paper broker → dashboard and audit log.

Python owns research, API contracts and evaluation. Rust holds deterministic performance-sensitive primitives, to be expanded only when profiling justifies it. TypeScript is the operator-facing web UI. No component currently routes real orders.

Controls: normalize timestamps/timezones; retain source timestamps; prevent look-ahead and survivorship bias; make ingestion idempotent; enforce position/notional/concentration/drawdown/stale-data/kill-switch checks; audit signals, decisions, rejections and simulated fills; never commit secrets.

The six research features are diagnostics only. Gamma is not dealer GEX without OI, contract multiplier, positioning sign and underlying price. Lagged correlation is not causal proof. Regime thresholds require instrument-specific calibration.