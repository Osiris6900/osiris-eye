# Package 41: apply stored corporate actions to the paper book

The instrument master already stored a RELIANCE 2-for-1 split on
2024-06-01 and an AAPL cash dividend of 0.25 on 2024-08-15. Neither changed a
paper position. `POST /paper/execution/corporate-actions` now applies due
master actions. The body may contain only `as_of`. A ratio or dividend in the
request is rejected.

## What is applied

Actions come from the seeded master, in effective-time order, once each.

- A split multiplies quantity and divides average cost. The product is
  unchanged, and the arithmetic must be exact. Cash is unchanged.
- A cash dividend adds `quantity * cash_per_share` to cash and realized P&L.
  Quantity and average cost stay the same.
- A flat book records a zero application. A later buy is not split by an
  action whose effective time already passed.
- Shares are adjusted only when every non-rejected order in that symbol was
  created before the effective time. A position that crosses the effective
  time is left unchanged and reported as `position_crosses_effective_time`.

An open order or a stored mark blocks that symbol. Symbol changes are
reported as `unsupported_corporate_action` and are not applied. A crash before
commit leaves the previous book in place. Replaying the journal reproduces
the cash, quantity, and cost. `LIVE_EXECUTION_ENABLED` stays false.

The catalog flag `corporate_actions_applied_to_positions` stays false. The
master does not know whether a particular paper book has applied its actions.
The paper snapshot lists the applications that book has recorded.
