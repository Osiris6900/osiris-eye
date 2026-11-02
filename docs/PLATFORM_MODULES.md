# Ten-module platform foundation

This commit adds deterministic Python primitives and tests for all ten planned modules. They are foundations, not complete institutional services.

| # | Module | Current foundation | Not yet implemented |
|---|---|---|---|
| 1 | Alternative Data Engine | Validated event envelope with event/publish timestamps and bounded sentiment | Licensed news, filings and macro ingestion; entity resolution; source lineage |
| 2 | Market Microstructure | Normalized displayed bid/ask volume imbalance | Order-book feed, queue position, trade classification and calibration |
| 3 | Portfolio Optimization | Long-only inverse-volatility baseline weights | Covariance-aware optimizer, constraints, turnover and transaction costs |
| 4 | Risk Intelligence | Historical VaR and expected shortfall primitives | Portfolio aggregation, stress scenarios, limits and independent sign-off |
| 5 | AI Model Factory | Numeric population-stability drift diagnostic | Training pipelines, registry, reproducibility, calibration and model governance |
| 6 | Strategy Discovery Lab | Periodic return, Sharpe and drawdown summary | Event-driven simulator, costs, walk-forward and untouched OOS evaluation |
| 7 | Execution Intelligence | Spread + slippage + impact cost estimator | Broker adapters, order state machine and paper execution; no live routing |
| 8 | Macro & Regime Engine | Explicit volatility/inflation/rates heuristic labels | Licensed macro series, release vintages, point-in-time joins and calibrated regimes |
| 9 | System Reliability Center | Component health aggregation | Metrics, alert delivery, durable audit log, backup/restore and incident runbooks |
| 10 | AI Agent Research Team | Typed role/task contract with human approval required | Agent runtime, sandboxing, permissions, review queue and audit trails |

## Safety and interpretation

- All calculations are deterministic research diagnostics. They do not establish an investment edge or constitute trade recommendations.
- VaR/ES are historical sample estimates and are sensitive to sample length and regime changes.
- Inverse-volatility weights are a simple baseline, not a covariance-aware portfolio optimizer.
- PSI and regime thresholds need instrument- and dataset-specific calibration.
- Alternative-data timestamps must be retained; features must use publication/availability time to avoid look-ahead bias.
- Agent tasks are explicitly approval-gated. This module does not place orders or execute arbitrary code.
- No market data provider, broker, credential store or live order path is connected.

## Next integration gates

1. Validate the Python, Rust and TypeScript CI jobs on GitHub.
2. Add licensed point-in-time market data adapters and an instrument/calendar master.
3. Persist immutable raw data and normalized data with freshness, gap and lineage checks.
4. Build event-driven backtesting with realistic costs and walk-forward/OOS evaluation.
5. Connect simulated paper execution only after risk, reconciliation and kill-switch tests pass.
