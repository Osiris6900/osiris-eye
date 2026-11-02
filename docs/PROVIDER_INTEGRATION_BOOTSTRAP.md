# Provider Integration Bootstrap

## Purpose

The provider onboarding catalog gives operators one consistent place to see which
market-data providers are planned and which environment-variable names are required.
It covers the cross-asset market families already supported by Osiris Eye:
equities, crypto, FX, futures, options, commodities, indices, and fixed income/rates.

## Initial provider profiles

- Interactive Brokers: multi-asset profile; product and venue permissions vary by account.
- Alpaca: equities, crypto, and options profile; availability varies by region/account.
- Binance and Coinbase: crypto profiles.
- OANDA: FX profile.
- Polygon and Twelve Data: multi-asset data profiles; exact instruments and data plans vary.

The catalog is an onboarding checklist, not a vendor endorsement or a guarantee of
coverage. Confirm current API products, country availability, exchange licensing,
redistribution rights, data delays, and account entitlements with each provider.

## Configuration

Set the environment variables named by
`GET /market-data/provider-readiness` in the deployment secret manager. Never commit
API keys, tokens, passwords, certificates, or account secrets to Git. The endpoint
returns variable names and presence/missing status only; it never returns values.

## API

- `GET /market-data/provider-readiness` returns provider profiles, supported asset
  class declarations, required environment-variable names, and configuration status.

A provider marked `configured: true` only means all listed variables are non-empty.
It does not mean the API credentials are valid, an account is authenticated, a market
data license is active, or a network connection works.

## Current limitations and safety

- No vendor SDK or real provider adapter is implemented in this phase.
- No outbound provider connection or credential validation is performed.
- `connectivity_verified`, `market_data_enabled`, and `live_execution` remain false.
- Existing provider adapter registration remains a separate trusted application
  configuration step; this catalog does not auto-register or activate adapters.
- Live trading must remain disabled until vendor-specific adapters, licensed data,
  paper trading, risk controls, and operational monitoring have been validated.
