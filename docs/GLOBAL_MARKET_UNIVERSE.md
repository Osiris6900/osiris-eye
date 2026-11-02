# Global Market Universe

Osiris Eye uses a provider-neutral cross-asset instrument metadata contract.

## Asset-class taxonomy

The initial taxonomy covers equities, crypto, foreign exchange, futures, options,
cash commodities, indices, and fixed income/rates. Examples are illustrative
venue/product categories, not a claim of exchange membership, data licensing,
or active connectivity.

## Instrument audit

- `GET /market-data/asset-classes` returns the taxonomy.
- `POST /market-data/instruments/audit` validates a batch of instrument metadata.
- The audit normalizes a canonical ID as `asset_class:venue:symbol`, validates
  the IANA timezone, positive tick and lot sizes, quote currency, and optional
  base currency / contract multiplier.
- Duplicate canonical IDs in a batch are rejected.
- This endpoint is validation-only: it does not persist a catalog or subscribe
  to an exchange.

Example payload:

```json
{
  "instruments": [{
    "asset_class": "equity",
    "venue": "NSE",
    "symbol": "RELIANCE",
    "quote_currency": "INR",
    "timezone": "Asia/Kolkata",
    "session_type": "exchange",
    "tick_size": "0.05",
    "lot_size": "1"
  }]
}
```

## Explicit limitations

This is the shared metadata foundation for an all-markets architecture, not an
all-markets data connection. Exchange calendars, instrument master ingestion,
corporate-action adjustment, vendor-specific symbol mapping, market depth,
streaming, persistent catalog storage, licensed data entitlements, and broker
connectivity remain separate future integrations. Live execution remains off.
