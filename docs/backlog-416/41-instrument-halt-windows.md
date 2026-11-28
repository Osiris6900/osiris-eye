# Instrument halt windows

A version whose `status` is `halted` or `delisted` stays `instrument_inactive`.
An active version may also carry up to eight half-open UTC halt windows,
`[start, end)`. During a window, orders, fills, marks and the pre-trade gate
return `instrument_halted`. The end instant is open again. Windows are stored
in UTC and must not overlap.

A version that has halt windows and is assessed without a decision time
returns `instrument_halt_unspecified`. A naive timestamp, an empty list, or
an end that does not follow the start is rejected when the version is stored.

The checked-in fixture has no halt windows. This is not an exchange halt feed.
`LIVE_EXECUTION_ENABLED` stays false.
