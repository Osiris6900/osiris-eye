# Package 41: exchange sessions are closed on weekends

Stored trading hours are a clock window. They do not know the day of the week,
so a Sunday 10:00 America/New_York print used to fill AAPL. Exchange sessions
(`session_type` `exchange`) now return `exchange_closed` on Saturday and Sunday
in the stored timezone, before the clock window is considered.

Continuous books, including the 00:00–23:59 crypto fixtures, are unchanged.
A version with no stored trading hours is unchanged. Official holidays are
still not implied: `exchange_holidays_enforced` stays false. A loaded
exchange-calendar holiday is a separate store and is not consulted here.

`LIVE_EXECUTION_ENABLED` stays false.
