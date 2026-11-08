# Paper execution and risk acceptance

Tracks #179 and #182. Live broker routing remains disabled.

- [ ] Define an order state machine: proposed, risk-rejected, accepted, partially-filled, filled, cancelled, expired.
- [ ] Require idempotency keys, durable event journal and restart-safe recovery.
- [ ] Simulate fills with spread, slippage, fees, latency, partial fills and liquidity constraints.
- [ ] Reconcile positions, cash, margin, fees and P&L against independently computed ledger totals.
- [ ] Verify kill switch, stale-feed fail-closed and exposure limits under fault injection.
- [ ] Connect research signals through risk approval to paper fills and dashboard audit trail in CI.

Separate authorization and security review are required before any live pilot.
