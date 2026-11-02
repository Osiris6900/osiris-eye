# Market Data Gateway — Provider Adapter Contract

This extends the existing Phase 4 local ingestion and point-in-time foundations.
It is a provider-neutral request/normalization boundary, not a connected market-data service.

## Current scope

- Explicit adapter registration; no vendor SDK or network client is bundled.
- Synthetic and sandbox adapters only. Live-mode adapters fail closed.
- An operator entitlement reference is required per provider. A reference is not
  independent proof of licensing; validate vendor contracts, exchange permissions,
  redistribution rights, and intended use separately.
- Normalizes OHLCV bars to uppercase symbols and UTC timestamps.
- Rejects non-finite values, booleans masquerading as numbers, invalid OHLC
  relationships, negative volume, duplicate timestamps, availability ordering
  errors, symbol mismatches, and bars outside the requested window.
- Returns provider/mode provenance and always marks live execution false.

## Adapter interface

Implement `name`, `mode` (`synthetic` or `sandbox`), and
`fetch_bars(symbol, start, end) -> list[dict]`. Inject adapters and
operator-supplied entitlement references into `MarketDataGateway`.
Never put API keys in source code or return them from status endpoints.

## API

- `GET /platform/market-data/providers` — configured adapter status.
- `GET /market-data/bars?provider=...&symbol=...&start=...&end=...` —
  normalized historical bars. By default, no provider is registered, so requests
  fail closed with HTTP 503.

## Not yet implemented

No real-time WebSocket/SSE stream, vendor-specific adapter, exchange calendar,
corporate-action adjustment, consolidated tape, tick/depth feed, persistent
market-data queue, or live order routing is included. SQLite batch ingestion
remains the local research store. Before adding a real vendor, implement its
adapter separately, verify its contract and rate limits, add fixture tests, and
complete security/compliance review.
