# Package 41: symbol changes on the paper book

Splits and cash dividends already apply. A stored `symbol_change` now renames
the paper position. Quantity, average cost, and cash stay the same. The new
symbol must already be a distinct instrument in the master. If that symbol
already has a position, or either symbol has an open order or a mark, the
action is skipped and the book is unchanged.

Fills keep the symbol they were printed with. Journal replay moves the
position after those fills. A second apply is a no-op. `LIVE_EXECUTION_ENABLED`
stays false. This does not rewrite historical fills or apply a merger.
