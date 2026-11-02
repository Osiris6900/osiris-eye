# Phase 11 — Production-Grade Trading Infrastructure

Eight deterministic research and paper-control foundations:
1. Portfolio Ledger & Reconciliation — position and cash break detection; breaks block new risk.
2. Advanced Order Lifecycle — validates accepted, partial, filled, cancelled and rejected states; no order routing.
3. Transaction Cost Calibration — summarizes supplied realized costs by bucket; not a live calibrated model.
4. Options Greeks & Volatility Risk — multiplier-adjusted aggregate Greeks; no surface repricing.
5. Margin & Funding Intelligence — supplied-funds versus maintenance-margin checks and simple funding estimate; broker rules not verified.
6. Portfolio Liquidity & Exit Planner — base/stressed exit days from supplied ADV and participation assumptions; no calibrated impact.
7. Model Deployment & Rollback Controller — shadow, performance, drift, risk and human-approval gates; no production deployment.
8. Trading Kill-Switch — blocks new risk on unhealthy components, risk breaches, unclear critical-data state or manual halt; does not cancel live orders.

## API routes
- POST /portfolio/ledger/reconcile
- POST /execution/orders/lifecycle-audit
- POST /research/execution/cost-calibration
- POST /risk/options/greeks-aggregate
- POST /risk/margin/funding-stress
- POST /portfolio/liquidity/exit-plan
- POST /platform/models/rollout-gate
- POST /platform/risk/kill-switch/evaluate

All endpoints consume caller-supplied data. Vendor/broker connectivity, licensed feeds, calibrated market impact, real order routing, automatic cancellation and production model deployment remain disabled. Registry entries are capability foundations, not production certification.
