# OSIRIS EYE: Five-stage delivery plan

This document separates implemented foundations from production capabilities that still require engineering, credentials, data rights, and verification.

## 1. Market data foundation
- Read-only public connectors: Coinbase Exchange candles, Binance Spot klines, and Stooq daily equities.
- Symbol-convention routing: `BASE-QUOTE` to Coinbase, concatenated crypto symbols such as `BTCUSDT` to Binance, and supported suffixed tickers such as `AAPL.US` to Stooq.
- Operator override: `OSIRIS_MARKET_PROVIDER_ORDER` can supply a comma-separated priority list.
- Every routed response retains provider provenance. Routing status explicitly reports that connectivity and licensing are not verified.
- Public adapters are subject to upstream rate limits, symbol availability, outages, and provider terms. They are not an institutional consolidated feed.

## 2. Universal data engine
- The universal aggregator preserves provider-specific bars, reports alignment and close-price divergence, and does not silently blend prices.
- Provider failures are surfaced; malformed market data fails closed.
- Remaining work: canonical instrument identifiers, exchange calendars per instrument, currency normalization, corporate-action adjustments, persistent raw-data archive, and scheduled data-quality monitoring.

## 3. Global market expansion
- Current implemented public coverage is limited to Binance Spot crypto, Coinbase Exchange crypto, and Stooq daily historical equities.
- Interactive Brokers is a separately configured paper-data adapter; it is not automatically activated.
- NSE/BSE, US exchange real-time feeds, FX, futures, options, indices, and commodities require provider-specific adapters, exchange entitlements, symbol masters, and licensing. Their coverage is not claimed complete.

## 4. Research and paper trading
- Existing research/backtest and paper workflow endpoints can evaluate signals and apply risk checks.
- Paper workflow explicitly records paper-only mode and does not submit broker orders.
- Remaining work: out-of-sample and walk-forward evaluation, realistic fees/slippage/borrow/funding, portfolio-level constraints, reproducible datasets, and persistent paper-trade monitoring before any strategy can be considered for production.

## 5. Live-readiness and operational controls
- Live market-data and live execution flags remain disabled.
- Existing risk, audit, kill-switch, and operational-gate modules are research/control foundations, not certification of production readiness.
- Before any live trading: licensed exchange/vendor data, broker authorization and entitlements, instrument mapping, execution adapter review, pre-trade risk limits, idempotency, reconciliation, kill-switch drills, security review, monitoring/alerting, disaster recovery, and explicit operator approval are mandatory.
- No profitability or return is guaranteed.

## Current change in this release
- The routed-bars endpoint no longer returns `multi_provider_routing_not_configured` merely because an operator priority list is absent. It selects a provider using the request symbol convention, while allowing an explicit operator override.
- This routing change is not a claim that the selected upstream is reachable. The live endpoint must still be smoke-tested in the deployed environment.
