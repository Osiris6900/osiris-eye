# Package 41: stored trading hours on the paper path

The instrument master already stored a local open, close and timezone for each
seeded contract. Paper orders, fills, marks and the pre-trade gate did not read
it. A RELIANCE buy at 15:00Z (20:30 Asia/Kolkata) filled. That order is now
`outside_trading_hours` and does not change cash, position or fill count.

## Window

Stored hours are a half-open local-minute window, `[open, close)`. AAPL
`09:30`–`16:00` America/New_York accepts 09:30 and 15:59 and rejects 16:00.
The decision time is the paper order or fill `now`, the mark `observed_at`, or
the pre-trade gate's decision clock. An exchange session with no caller clock
is `trading_hours_unspecified`. A stored window that cannot be applied is
`trading_hours_unavailable`.

Continuous books seeded as `00:00`–`23:59` stay open for the whole civil day,
including 23:59. The master cannot store `24:00`, and that pair is the fixture's
24-hour session. A version with no `trading_hours` is unchanged.

## What this does not do

The window is not the exchange holiday calendar and not a weekend calendar. A
Sunday print inside 09:30–16:00 America/New_York is still accepted. Corporate
actions remain stored and unapplied. `live_trading_authorized` is not set, and
`LIVE_EXECUTION_ENABLED` stays false.

`GET /market-data/instruments/catalog` reports `trading_hours_enforced: true`
and `exchange_holidays_enforced: false`.
