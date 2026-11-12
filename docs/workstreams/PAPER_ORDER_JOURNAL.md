# Paper order journal and projection integrity

Work packages #416/44, #416/45, #416/47 and #416/57; integration #364 and #412.

New paper accounts record `osiris.paper_order_journal.v2` in the first hashed
initialization event. The event binds the initial cash and persisted risk/clock
policy. Order decisions retain their economics, mode, creation time and decision
hash. Fill events retain quantity, price, fee, side, symbol, realized P&L and
resulting state. Mark events retain both prices and their observation timestamp.

Every normal write and reconciliation verifies the existing hash chain, replays
the financial events, and compares the replayed orders, fills, marks and kill
switch against their tables. Initial cash and risk policies must match their
initialization evidence. Arithmetic cash/position/P&L reconciliation remains in
place. A balanced rewrite of several tables cannot satisfy the journal contract
merely by preserving the final cash identity.

Examples now rejected include changing a working fill's fee and compensating
cash/cost basis, deleting a balanced round-trip history, unaudited cancellation,
clearing a persisted kill switch, increasing a limit, or refreshing a mark's time
without an event. Reconciliation failure blocks new orders and fills, and the
Admin Centre withholds the affected account values.

Ambiguous control values also fail closed. `live_execution` must be literal
`false`; `working` must be a boolean; only literal `false` leaves the per-order
kill switch clear. JSON strings, integers, nulls, arrays and objects cannot
silently choose an execution mode or bypass a gate. Idempotent reads of prior
orders continue to return the committed historical outcome without a new fill.

## Legacy and operational boundaries

Historical journals that did not retain complete fill fees, mark timestamps or
risk policy remain readable under their previous arithmetic contract. Reports
explicitly identify them as `journal_binding=legacy_unbound`; they are never
relabeled as version 2 or silently upgraded by inventing historical events.
An existing account needs a separately reviewed history migration or a fresh
paper account to obtain the new contract. Writers must use a consistent version:
old binaries writing incomplete events into a v2 account cause reconciliation
to fail closed.

This is local tamper evidence. It does not detect a privileged actor replacing
the entire database and recomputing every hash, or a coordinated rollback of the
database and its local records. Independent checkpoints, custody and security
review remain necessary for that scope. No live trading permission is granted.

## Reproducible verification

Run `pytest` against the API suite using the package installed from this checkout.
When sharing an environment across worktrees, also set `PYTHONPATH` to this
checkout's `services/quant-api/src` so subprocess tests use the same writer.
The journal regressions retain a valid hash chain while altering projections,
exercise partial fills/cancellation/marks/kill-switch replay, and verify crash
rollback and the explicit legacy boundary.
