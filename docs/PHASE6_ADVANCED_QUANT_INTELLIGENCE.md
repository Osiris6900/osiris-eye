# Phase 6 — Advanced Quant Intelligence

Phase 6 adds six prototype research capabilities to the FastAPI service. All outputs are diagnostics and must not be interpreted as trade instructions, guaranteed forecasts, or production approvals.

## Systems and API routes

| System | Endpoint | Contract |
|---|---|---|
| Walk-Forward Validation Engine | `POST /research/validation/walk-forward` | Chronological train/test folds; no shuffling. Fold summaries do not replace an untouched final OOS set. |
| Advanced Portfolio Risk Engine | `POST /research/portfolio/advanced-risk` | Weighted multi-asset returns, annualized volatility, max drawdown, gross leverage and HHI concentration. |
| Execution Intelligence Engine | `POST /research/execution/intelligence` | Participation and square-root impact heuristic; flags participation above configured capacity. |
| AI Model Validation & Drift Engine | `POST /research/models/advanced-validation` | Feature PSI and accuracy-drop threshold checks; deployment is never authorized by this endpoint. |
| Global Macro Intelligence Engine | `POST /research/macro/intelligence` | Transparent rule-based regime labels from caller-supplied changes; no macro feed is connected. |
| Strategy Intelligence & Ranking Engine | `POST /research/strategies/rank` | OOS Sharpe, drawdown, turnover and trial-count penalty for research triage only. |

## Example requests

Walk-forward:

```json
{"returns":[0.01,-0.02,0.015,0.004,0.008,-0.01,0.02,0.01],"train_size":4,"test_size":2}
```

Portfolio risk:

```json
{"returns":{"A":[0.01,-0.02,0.03],"B":[0.0,0.01,-0.01]},"weights":{"A":0.6,"B":0.4},"periods_per_year":252}
```

Execution heuristic:

```json
{"quantity":1000,"adv":100000,"half_spread_bps":2,"volatility":0.02,"impact_coefficient":1.0,"max_participation":0.1}
```

Model validation:

```json
{"reference":[0.1,0.2,0.3,0.4],"current":[0.2,0.3,0.4,0.5],"reference_accuracy":0.8,"current_accuracy":0.72}
```

Macro classification:

```json
{"inflation_change":0.1,"rates_change":0.2,"growth_change":-0.1,"volatility_change":0.3}
```

Strategy ranking:

```json
{"candidates":[{"name":"candidate-a","oos_sharpe":1.1,"max_drawdown":-0.12,"turnover":4,"trials_tested":20}]}
```

## Research and safety limitations

- Walk-forward folds are chronological but do not automatically purge/embargo overlapping labels, account for all feature publication lags, or reserve an untouched final test period. The response explicitly states that no untouched final test set was used.
- Portfolio risk uses caller-supplied aligned periodic returns and static weights. It is not a full covariance optimizer, tail-risk model, liquidity stress engine, or dynamic rebalancer.
- Execution impact is an uncalibrated square-root heuristic. It omits order-book depth, venue routing, queue priority, partial fills, volatility regime calibration, fees and market-specific constraints. It never sends orders.
- PSI is a coarse distribution-shift diagnostic. Accuracy inputs require consistent, mature labels and comparable evaluation windows. No model is deployed or approved automatically.
- Macro labels are rule-based and only as reliable as the caller's timestamped, point-in-time inputs. No economic release calendar or vendor feed is connected.
- Strategy ranking is vulnerable to selection bias and data snooping. Trial-count penalty is a simple heuristic, not a formal multiple-testing correction. Require walk-forward, untouched OOS, costs, survivorship controls and paper monitoring.
- Registry entries are prototype capability indicators, not production-readiness claims. Existing deterministic risk controls remain independent; agents and research models cannot bypass them. Live broker connectivity and order routing remain disabled.
