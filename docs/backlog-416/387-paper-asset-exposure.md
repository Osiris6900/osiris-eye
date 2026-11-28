# #387: cross-asset classification of the paper book

`GET /paper/execution/asset-exposure` reads reconciled holdings and joins each
symbol to the instrument-master asset class. Marked notional is summed by
class. Each position has one edge, `classified_as`, to that class.

The edges are not correlations, betas, or hedges.
`correlation_estimated` stays false. A caller weight or asset class on the
query string is ignored. A missing or stale mark, or a symbol whose class
cannot be resolved, returns `incomplete` and no total. A corrupt audit
returns `unavailable`. The call does not write the book.
`LIVE_EXECUTION_ENABLED` stays false.
