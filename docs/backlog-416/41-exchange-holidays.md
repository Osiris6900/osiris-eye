# Package 41 / #386: sourced exchange holiday closes

Exchange sessions already close on Saturday and Sunday. Weekday holidays now
come from a checked-in calendar.

## Sources

- XNYS, full-day closes for 2024-01-01 through 2026-12-31: NYSE Group /
  Intercontinental Exchange announcement of 10 November 2023,
  <https://ir.theice.com/press/news-details/2023/NYSE-Group-Announces-2024-2025-and-2026-Holiday-and-Early-Closings-Calendar/default.aspx>.
- NSE capital-market weekdays in 2026: circular NSE/CMTR/71775 (12 December
  2025), <https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf>,
  plus 15 January 2026 from NSE/CMTR/72260 (12 January 2026),
  <https://nsearchives.nseindia.com/content/circulars/CMTR72260.pdf>.

BSE is not an instrument-master venue, so those NSE circulars are not copied
onto it. Early closes (the day after Thanksgiving, Christmas Eve, and the
July 3 sessions the NYSE notice marks as 1:00 p.m. closes) are not full-day
holidays and are not seeded. NSE muhurat trading on Sunday 8 November 2026
had no regular-session time in the cited circular, and that Sunday is already
closed.

## Enforcement

Orders, fills, marks, and the pre-trade gate use the decision time. A seeded
holiday returns `exchange_holiday`. A date outside that venue's coverage
returns `exchange_calendar_out_of_range`. An exchange venue with no seeded
calendar returns `exchange_calendar_unavailable`. A holiday applies only after
its source's publication time, so the 15 January 2026 NSE addition is not in
force before 12 January 2026.

Continuous books are unchanged. `LIVE_EXECUTION_ENABLED` stays false. This is
not a live circular feed.
