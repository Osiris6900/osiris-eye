# Phase 5 — Institutional Portfolio Backtesting Foundation

## Delivered
- Multi-asset portfolio return simulation with explicit one-period lag: target weights at t-1 are applied to returns at t.
- Signed portfolio weights, gross exposure ceiling, and initial capital validation.
- Separate commission, spread, slippage and impact assumptions, charged on absolute portfolio turnover.
- Equity curve, total return, CAGR, annualized volatility, Sharpe diagnostic, max drawdown and turnover.
- Deterministic API contract and regression tests.

## Endpoint
`POST /research/backtest/portfolio`

Example payload:
```json
{
  "asset_returns": [{"A": 0.0, "B": 0.0}, {"A": 0.01, "B": -0.02}, {"A": 0.02, "B": 0.01}],
  "target_weights": [{"A": 0.5, "B": 0.5}, {"A": 0.5, "B": 0.5}, {"A": 0.0, "B": 1.0}],
  "commission_bps": 1,
  "spread_bps": 2,
  "slippage_bps": 1,
  "impact_bps": 1,
  "periods_per_year": 252,
  "max_gross_leverage": 1.0,
  "initial_capital": 100000
}
```

## Important limitations
This is a deterministic portfolio research simulator, not a production fill simulator. It does not model queue position, partial fills, calibrated nonlinear market impact, borrow availability/cost, funding, taxes, capacity, corporate actions or delistings. Input returns must already be correctly adjusted and point-in-time. No vendor feeds, broker integration, or live order routing are added. Results are diagnostics, not validated forecasts or promises of returns.

Before institutional use, add event-driven order/fill simulation, instrument-specific fee schedules, corporate-action and futures-roll processing, borrow/funding, market impact calibration, benchmark/attribution, walk-forward orchestration, and independent out-of-sample governance.
