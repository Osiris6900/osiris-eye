# Multi-provider market-data routing

This layer adds deterministic priority-order failover over the existing provider-neutral gateway.

## Behaviour
- Operators inject a configured `MarketDataGateway` and an explicit ordered provider list.
- Each provider must independently have a registered adapter and operator-supplied entitlement reference in the gateway.
- The router attempts providers in the exact configured order.
- It fails over only on `MarketDataUnavailable` (missing adapter/entitlement, disabled live mode, or provider fetch failure).
- Invalid, malformed, duplicate, or out-of-window market data raises a validation error and is **not** silently hidden by trying another source.
- Successful responses record the selected provider and preceding provider failure reasons.
- Provider status is returned in priority order without exposing credentials.

## Security and operational limits
No API keys or secrets are stored in this router. Supply credentials through the deployment secret manager and implement vendor-specific adapters separately. An entitlement reference is operator evidence, not independent licensing verification. Confirm exchange licensing, redistribution, rate limits, and permitted use with each vendor.

This is a routing foundation, not a live feed. Existing gateway policy still permits only synthetic/sandbox adapters; live provider mode and live execution remain disabled. No vendor connection is claimed by this PR.
