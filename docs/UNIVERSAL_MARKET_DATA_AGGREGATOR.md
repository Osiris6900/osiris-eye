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


## Public connector rollout

The current read-only public connector set is:
- `coinbase-public`: Coinbase Exchange crypto candles; configurable granularity.
- `binance-public`: Binance Spot klines; default interval `1m`, configurable with `OSIRIS_PUBLIC_BINANCE_INTERVAL`. Uses public market-data endpoint only.
- `stooq-daily`: daily equity history for Stooq-supported symbols that include an explicit market suffix, for example `AAPL.US`. This is historical daily data, not an intraday feed.

Enable/disable public connectors with `OSIRIS_PUBLIC_COINBASE_ENABLED`,
`OSIRIS_PUBLIC_BINANCE_ENABLED`, and `OSIRIS_PUBLIC_STOOQ_ENABLED` (default true).
The endpoint `GET /market-data/global-coverage` lists implemented connectors and
explicitly reports that complete global coverage and all-market real-time status are
not verified.

Examples:
- Crypto: `providers=binance-public,coinbase-public&symbol=BTCUSDT` is not a valid
  cross-provider symbol because Coinbase and Binance use different product identifiers.
  Query each provider using its native symbol (`BTCUSDT` vs `BTC-USD`) in separate
  requests until a reviewed symbol-mapping layer is implemented.
- US equity daily: `providers=stooq-daily&symbol=AAPL.US`.

Do not assume a symbol exists on every venue. No connector is allowed to imply that
all listings, delayed/live status, redistribution rights, or exchange entitlements are
available. API keys are not needed for these public endpoints. Do not send order
requests through these data adapters.
