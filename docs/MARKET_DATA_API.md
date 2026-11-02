# Market Data API Integration

The FastAPI application exposes the provider-neutral gateway through authenticated routes:

- `GET /market-data/providers` — provider modes, configuration and operator entitlement-reference presence.
- `GET /market-data/bars?provider=...&symbol=...&start=...&end=...` — normalized historical OHLCV bars.

Both routes use the existing API bearer-token middleware. Configure `OSIRIS_API_TOKEN` in production; requests without a valid bearer token are rejected. The default gateway is intentionally empty: no market-data vendor, SDK, credentials, or network feed is configured by this change.

Only explicitly registered `synthetic` and `sandbox` adapters with an operator-supplied entitlement reference can serve bars. `live` provider mode remains blocked. Missing adapters or entitlement references fail closed. Provider status never exposes secrets and does not independently verify entitlement.

This integration does not enable live trading, order routing, real-time streaming, or any claim of exchange connectivity. The bars endpoint is historical-request/response only.
