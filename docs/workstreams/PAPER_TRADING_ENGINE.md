# Automatic Paper Trading Engine

Deliverable: deterministic sandbox OMS and accounting, with no live order routing.

## Interfaces
- Order intent: idempotency key, strategy ID, instrument, side, quantity, limit and expiry.
- Order state machine: created, accepted, rejected, partially filled, filled, canceled, expired.
- Ledger: fills, fees, cash, positions, realized/unrealized P&L and reconciliation.

## Acceptance
- Duplicate intent and restart replay cannot create duplicate simulated orders.
- Stale feed, risk breach and emergency kill switch block new orders.
- Reconciliation, partial fills, fees and disconnect recovery pass integration tests.

Tracking: #207. This file defines implementation contracts, not a live trading engine.
