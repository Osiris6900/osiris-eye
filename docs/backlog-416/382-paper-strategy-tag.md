# Immutable strategy tag on a paper order

Paper orders did not carry a strategy, so capital usage and attribution could
not split the book without trusting a caller list. `strategy_id` is now an
optional tag on `POST /paper/execution/orders`. It is stored in the
`order_decided` audit payload. A repeat of the same client order id must send
the same tag. A different tag, or dropping a tag that was stored, is
`idempotency_key_conflict` and does not create a second order.

The tag is one to sixty-four characters from `A-Z`, `a-z`, `0-9`, `_` and `-`.
Anything else is `invalid_strategy_id`. Omitting it leaves the order untagged.
`get_paper_order` returns the stored tag. This does not allocate a budget and
does not authorize live trading.
