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

## Market Mechanics diagnostics

The registry now includes ten additional research prototypes, bringing the total to 107. These extend the original foundations above with caller-data calculations and a dashboard research panel. Evidence labels describe support for a mechanism, not validation of a predictive trading strategy.

| Module | Calculation | Interpretation limit |
|---|---|---|
| Dealer Gamma Exposure (GEX) | Signed dealer gamma exposure and hedge sensitivity | Dealer positions must be supplied; option type does not determine the dealer's position sign |
| Options Expiry & Max Pain | Strike-level settlement payouts and minimum aggregate intrinsic payout | Expiry effects are mixed; Max Pain is a weak predictor |
| Liquidity Vacuum | Book sweep cost and unfilled quantity | A static displayed book omits hidden liquidity, cancellations and replenishment |
| Order Flow Imbalance | Buyer/seller aggressor-volume imbalance | Short-horizon association depends on classification and market conditions |
| Dark Pool Trading | Supplied off-exchange activity relative to total activity | Volume does not identify institutional buying or selling direction |
| Funding Rate & Liquidation Clusters | Per-period funding and caller-defined liquidation scenarios | Cluster inventories are assumptions, not observed exchange positions |
| Treasury Liquidity & Repo Market | Fed-assets/TGA/RRP liquidity proxy and repo spread | The proxy is incomplete and does not imply a causal risk-asset forecast |
| Index Rebalancing | AUM-weight-change flow and ADV participation | Implementation timing, anticipatory trading and actual fund tracking vary |
| Volatility-Control Funds | Target-volatility leverage and rebalance flow | Hypothetical fund rules and volatility inputs determine the estimate |
| Cross-Asset Lead–Lag | Chronologically aligned lagged correlations | Historical correlations can change; lag selection creates multiple-testing bias |

See [Market Mechanics](MARKET_MECHANICS.md) for the ten endpoint contracts, calculation conventions and editable API examples. The catalog is available at `GET /research/market-mechanics/catalog`; all new diagnostics remain research-only with execution disabled.
