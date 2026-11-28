# Package 50: stress the canonical paper book

`POST /research/portfolio/stress` shocks weights the caller supplies. It does
not read the paper book. `POST /paper/execution/stress` reads reconciled
positions and marks. The body may contain only `as_of` and `shocks`. A
replacement `positions` list is rejected.

A shock is a decimal string strictly above -1 and at most 5. `BTC: "-0.10"`
moves that holding's mark by minus 10 percent. `"*"` shocks every holding and
cannot be combined with a named symbol. A shock for a symbol that is not held
is rejected. A held symbol with no shock is rejected. A missing or stale mark
returns `incomplete` and no total.

The call does not write the book. `book_mutated` is false. `live_execution`
is false. A corrupt audit returns `unavailable`. This is a mark shock of the
current paper inventory, not a macro scenario, a liquidity haircut, or a
historical crisis replay.
