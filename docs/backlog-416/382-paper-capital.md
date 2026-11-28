# Package 382: capital usage of the paper book

`POST /portfolio/capital/risk-budget` weights strategies the caller supplies.
It does not read the paper book, and those weights are not an approved budget.

`GET /paper/execution/capital` reads the reconciled account. Marked inventory
plus resting buy orders is the gross exposure. Equity is cash plus marked
market value, and it must equal initial cash plus realized and unrealized
P&L. Inception drawdown is the loss of that equity from initial cash, divided
by initial cash. A gain has drawdown zero.

The approved limits are the ones stored on the account:

- gross exposure above `max_gross_exposure` is `exposure_budget_exceeded`
- adverse P&L at or below `-max_daily_loss` is `daily_loss_limit_reached`
  (`realized_plus_adverse_unrealized`, the same basis as order checks)
- one name, including its resting buys, above `max_position_notional` is
  `max_position_notional_exceeded`

A missing or stale mark on an open position returns `incomplete` and no
totals. A corrupt audit or a reconciliation break returns `unavailable`.
Query fields such as `strategies`, `budget` and `drawdown` are ignored.

`strategy_budgets_enforced` stays false. Paper orders have no strategy tag,
so this report does not split the book into caller-named strategies.
`LIVE_EXECUTION_ENABLED` stays false. The call does not write the book and
does not cancel an order that has already breached a limit.
