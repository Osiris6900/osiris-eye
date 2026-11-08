# AI Trading and Portfolio Brain

Deliverable: reproducible strategy and portfolio research with independent risk governance.

## Interfaces
- Features are versioned, timestamped, and constructed without future-data leakage.
- Models have immutable training datasets, seeds, version IDs and rollback metadata.
- Portfolio proposals include rationale, risk budget, instrument, target exposure and expiry.

## Acceptance
- Purged walk-forward tests, untouched out-of-sample evaluation, fees, slippage and stress scenarios.
- Explicit no-trade decisions on stale data, model drift or violated risk budgets.
- Never equate backtest success with future profitability.

Tracking: #207. This file defines implementation contracts, not a trained model.
