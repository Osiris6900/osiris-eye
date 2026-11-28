# Package 54: cash collateral on the paper book

`POST /risk/margin/funding-stress` uses caller balances. The paper book had
no collateral view. `GET /paper/execution/collateral` reads reconciled cash,
open buy reservations, and fresh marks.

Free cash is cash minus cash reserved for open buys. The margin loan is zero
because this book does not borrow. The model name is `cash_fully_funded`.
Long market value is the sum of fresh marks. A missing or stale mark leaves
that value null and the state `incomplete`, while cash is still reported.
Negative cash or free cash is a `cash_collateral_breach`.

A caller `cash` or `positions` query does not change the figures. A corrupt
audit returns `unavailable` and no cash figure. The call does not write the
book. `live_execution` stays false. This is not a broker margin schedule, a
portfolio-margin offset, or a borrowed-cash facility.
