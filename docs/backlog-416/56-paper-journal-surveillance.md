# Package 56: surveillance reads the paper journal

`POST /surveillance/trades/analyze` scores events the caller supplies. It does
not see the paper book. `GET /paper/execution/surveillance` reads the canonical
journal and orders after the audit chain and reconciliation pass.

Query parameters are ignored. `POST` to this path is not defined, so a caller
cannot submit a substitute event list. Thresholds are fixed: three or more
accepted orders in a symbol, a cancel ratio of at least 0.8, three submits
whose timestamps fall inside one second, and a buy and sell fill at the same
price and quantity. These are review indicators. `human_review_required` stays
true and `enforcement_action_taken` stays false.

A missing database, an uninitialized account, a corrupt audit chain, a
reconciliation break, or a counted order with no timestamp returns
`unavailable` and no alerts. An empty reconciled book is `reviewed` with an
empty alert list. `LIVE_EXECUTION_ENABLED` stays false. This is not #383's
full compliance program and it does not decide misconduct.
