# #416 package 48 — Broker snapshot input validation

Independent broker evidence must satisfy its input contract before comparison.
A matching digest does not establish a valid book shape.

- `positions`, `fills`, and `open_orders` are required JSON arrays. Empty arrays
  are valid; empty objects and strings cannot stand in for them.
- Each normalized position symbol occurs once, including zero balances.
  `btc` and `BTC` refer to the same symbol. Zero rows remain valid when unique.
- `live_trading_authorized`, when present, is `null` or the boolean `false`.
  Numeric zero and other false-like values are not accepted as booleans.
- `as_of` must convert to UTC within the supported datetime range. Extreme
  timezone offsets that overflow that range are invalid evidence.
- Fixture and control-plane JSON bodies are limited to 1 MiB of UTF-8 text.
  Parsed evidence uses the control-plane encoder's existing depth limit of 32,
  node limit of 200,000 and finite-JSON contract. Broker-book string trees retain
  their stricter 100,000-node limit and decimal-string requirements.

Invalid JSON, excessive nesting, unsupported structure, and invalid text produce
`snapshot_unverifiable`. This applies to the recorded fixture, the control-plane
`broker-book/account` body, and retained `broker-orders` bodies. A parser recursion
failure cannot leave the previous clean result as the latest reconciliation.

When the canonical account and audit chain are valid, reconciliation persists an
`unreconciled` result with `new_risk_allowed=false`. New orders and fills remain
blocked until a later valid comparison is clean. Cancellation and expiry retain
their existing ability to release local reservations during a broker halt.

Validation reads recorded evidence and makes no broker HTTP request. It does not
prove source truth, add a live account feed, continuously refresh a clean result,
or provide independent release acceptance. Live execution stays disabled.
