# Universal Market Data Aggregator

The Universal Market Data Aggregator is a provider-agnostic layer above the existing
Market Data Gateway. It accepts an ordered provider list, fetches normalized OHLCV
bars from each registered adapter, preserves each provider dataset, and selects the
first successful provider as the primary series.

## Endpoint

`GET /market-data/aggregate/bars`

Required query parameters: `providers` (comma-separated registered provider IDs),
`symbol`, `start`, and `end` (ISO-8601 timestamps with timezone).

Optional: `asset_class` and `divergence_threshold_bps` (default 50 bps).

## Data integrity rules

- Every provider is independently validated by MarketDataGateway.
- The primary series is the first successful provider in the requested order.
- Provider datasets and source provenance are retained in the response.
- Close-price divergence is reported only for exact matching candle timestamps.
- Prices from different venues are never silently averaged or blended.
- An unavailable provider is reported; malformed or invalid OHLCV data fails closed.
- No order placement or live execution is performed.

## Coverage limits

This is an aggregation framework, not a claim of all-world or institutional real-time
coverage. Only adapters actually registered and entitled/configured can return data.
Provider licensing, exchange permissions, delays, symbol mapping and rate limits remain
provider-specific.
