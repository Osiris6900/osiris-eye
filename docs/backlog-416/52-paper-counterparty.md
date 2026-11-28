# Package 52: counterparty exposure of the paper book

`POST /research/risk/counterparty` scores exposures the caller supplies.
`GET /paper/execution/counterparties` reads reconciled paper positions. The
counterparty is the instrument-master venue, which is the only identity the
book has. BTC and ETH both sit on `PAPER`, so their marked notionals add.

A missing or stale mark, or a symbol whose venue cannot be resolved, returns
`incomplete` and no gross total. A corrupt audit returns `unavailable`. Caller
`exposures` and `counterparty` query fields are ignored. The call does not
write the book.

`creditworthiness_assessed` stays false. Cash is not a counterparty. This is
not a credit limit, a netting set, or a legal-entity hierarchy.
`LIVE_EXECUTION_ENABLED` stays false.
