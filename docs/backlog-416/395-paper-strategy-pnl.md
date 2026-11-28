# Realized P&L by the paper order's strategy tag

`GET /paper/execution/strategy-pnl` reads the reconciled book. Each fill is
grouped by the `strategy_id` stored on its order decision. A fill whose order
has no tag is `strategy_id: null`. The group sums must equal the account's
realized P&L.

Realized P&L stays on the fill that booked it. A buy usually books zero, and
the closing sell books the residual, so a sell tagged differently from its
opening buy keeps that residual. Unrealized P&L is not split across tags,
because one position can mix them. No per-strategy budget is enforced.

A missing database, an uninitialized account, a corrupt audit chain, or a
reconciliation break returns `unavailable` and no rows. Query parameters are
ignored. `LIVE_EXECUTION_ENABLED` stays false. The call does not write the book.
