# Multi-Provider Market Data Connectors

## Design

Osiris Eye uses a provider registry between the API and vendor-specific adapters.
Each adapter implements the small `fetch_bars(symbol, start, end)` contract and
normalizes its upstream response into the gateway's timestamped OHLCV input.
Provider IDs are explicit allow-list entries; duplicate IDs and unknown asset
classes are rejected.

Supported asset classes are equities, crypto, FX, futures, options, cash
commodities, indices, and fixed income/rates. A provider registration declares
which of these it actually covers. Requests can include `asset_class`; the
gateway rejects a request when the selected provider has not declared support.

## Credentials and entitlements

Registrations accept secret references (for example, a secret-manager key or
environment-variable identifier), never raw secret values. API responses expose
only whether a reference is present. The current registry is in-memory and must
be populated by trusted application configuration; there is no public endpoint
for adding adapters or changing credentials.

An entitlement reference is an operator assertion, not proof that a vendor has
granted a valid license. Operators must separately verify contract, exchange
permissions, redistribution rights, data delay, and account entitlements.
Do not commit API keys, tokens, certificates, or vendor secrets.

## Safety and current scope

- Multiple adapters can be registered side by side and selected per request.
- Provider capabilities are checked before a fetch.
- Unknown providers, missing entitlement references, unsupported asset classes,
  and provider errors fail closed.
- Only synthetic and sandbox adapter modes can serve data.
- Live provider mode and live order execution remain disabled.
- No vendor SDK or production vendor adapter is bundled in this phase. Real
  connections require a chosen vendor, its current API documentation, valid
  credentials, and the required market-data licenses/entitlements.
- The registry is process-local; registrations must be supplied on each app
  startup. Durable provider configuration, secret-manager integration,
  streaming subscriptions, rate-limit scheduling, and failover orchestration
  are future work.

## API

- `GET /market-data/providers` lists configured provider capabilities and
  reference-presence flags, never secret values.
- `GET /market-data/bars?provider=...&symbol=...&start=...&end=...&asset_class=...`
  requests normalized bars. `asset_class` is optional for backward
  compatibility, but should be supplied by multi-asset clients.

The endpoint reports `live_execution: false`. Sandbox tests do not establish
production feed quality, latency, completeness, or licensing.
