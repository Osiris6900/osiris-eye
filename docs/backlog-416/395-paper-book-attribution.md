# Portfolio attribution of the canonical paper book

`GET /control-plane/attribution` groups realized P&L, fees and arrival slippage by a
strategy tag stored outside the paper ledger. It does not attribute unrealized
price movement, and a missing tag becomes `unattributed`.

`GET /paper/execution/attribution` reads the reconciled paper book. Each symbol
that has a fill or an open position is one row:

- `realized_pnl` is the sum of that symbol's fill residuals.
- `fees` is the sum of that symbol's fill fees. Buy fees sit in average cost,
  so they reduce unrealized (and later realized) rather than the current
  realized residual.
- `unrealized_pnl` is quantity times mark minus average cost, using the same
  closing-settlement arithmetic as the account.
- `price_movement` is `realized_pnl + unrealized_pnl + fees`.

The symbol sums must equal `paper_exec_meta.realized_pnl` and
`unrealized_pnl`. A mismatch, a corrupt audit chain, or a reconciliation break
returns `unavailable` and no totals. An open position with a missing or stale
mark returns `incomplete` and no totals. A flat book does not need a mark.
Query fields such as `strategy`, `slippage` and `pnl` are ignored.

`strategy_attributed` and `slippage_attributed` stay false. This report does
not join the control-plane tag store and does not treat the order limit as an
arrival benchmark. `LIVE_EXECUTION_ENABLED` stays false. The call does not
write the book.
