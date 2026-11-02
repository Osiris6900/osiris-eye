# Global Exchange Calendar Foundation

OSIRIS EYE now has a provider-neutral, versioned calendar store for all supported asset classes and venues. It supports IANA time zones, local session windows, overnight sessions, holidays, early closes/special sessions, and point-in-time versions.

## API

- `POST /market-data/calendars/sessions`: append a calendar record.
- `GET /market-data/calendars/sessions?venue=XNYS&calendar_date=2026-11-03&as_of=...`: read the latest version known as of a timestamp.
- `GET /market-data/calendars/status?venue=XNYS&at=2026-11-03T15:00:00Z&as_of=...`: evaluate whether the venue is in a configured session.

Example import:

```json
{
  "effective_at": "2026-01-01T00:00:00Z",
  "record": {
    "venue": "XNYS",
    "timezone": "America/New_York",
    "calendar_date": "2026-11-03",
    "is_holiday": false,
    "sessions": [{"open": "09:30", "close": "16:00"}],
    "source": "licensed-calendar-feed-or-operator"
  }
}
```

A close time less than or equal to its open time means the session closes on the following local date. Early closes are represented by that date's shorter session. Holidays have no sessions. Timestamps must include a timezone; local session times use the venue IANA timezone, with nonexistent DST wall times rejected.

## Persistence and trust boundaries

SQLite path is configurable with `OSIRIS_CALENDAR_DB_PATH`, defaulting to `data/osiris_exchange_calendar.sqlite3`. Records are append-only and unique per venue/date/effective timestamp. Deployments must mount persistent storage and back it up.

This is a calendar engine, not an official worldwide holiday database. Initial records must come from an operator or trusted/licensed source; provenance is retained. No global calendar vendor is bundled, no exchange connectivity is implied, and unknown dates return `calendar_data_unavailable` rather than being assumed open or closed. Live execution remains disabled.
