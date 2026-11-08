# Global Market Intelligence

Deliverable: licensed and auditable multi-asset data adapters for equities, futures, options, FX and crypto.

## Interfaces
- Instrument identity: venue, symbol, asset class, currency, expiry and exchange calendar.
- Observation envelope: source, entitlement, UTC event timestamp, ingestion timestamp, sequence, revision and quality flags.
- Feed contract: reconnect, backfill, deduplication, staleness, gap detection, corporate actions and data provenance.

## Acceptance
- Deterministic replay and point-in-time tests.
- Authorized vendor and redistribution review before real feed integration.
- Stale or malformed data must fail closed before any trading decision.

Tracking: #207. This file defines implementation contracts, not completed adapters.
