# Persistent Instrument Master

SQLite-backed, provider-neutral catalog for all supported asset classes. Configure
`OSIRIS_INSTRUMENT_DB_PATH`; default: `data/osiris_instruments.sqlite3`.

- `POST /market-data/instruments`: persist a validated version. Body:
  `{"instrument": {...}, "source": "operator", "effective_at": "2025-01-01T00:00:00Z"}`.
- `GET /market-data/instruments`: search by asset_class, venue, symbol, as_of, limit.
- `POST /market-data/instruments/provider-mappings`: append provider-symbol mapping.
- `GET /market-data/instruments/provider-mappings/resolve`: point-in-time mapping resolution.

Metadata versions and mappings are append-only. Existing instrument/effective-time
pairs cannot be overwritten. As-of reads select the latest effective version.
Option contracts require expiry, CALL/PUT and positive strike. Source labels and
recorded timestamps are retained.

This release supports operator/fixture imports only. No licensed vendor instrument
master, exchange calendar, real-time feed, broker connection, or live execution is
configured. Back up the SQLite data volume in deployment.
