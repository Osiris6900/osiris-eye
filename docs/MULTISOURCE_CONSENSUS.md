# Multi-Source Consensus & Source Health Weights

> Accuracy upgrade: canonical instruments now have genuinely independent
> sources, and the mesh weighs each source by its measured agreement
> history — bounded, transparent, and never able to override the quorum or
> inlier gates.

## New keyless providers (global_market_watch)

| Provider | Endpoint | Covers |
|---|---|---|
| `coinbase-public` | `api.exchange.coinbase.com/products/<X>-USD/ticker` | BTC, ETH, SOL, XRP |
| `kraken-public` | `api.kraken.com/0/public/Ticker` | BTC, ETH, SOL, XRP |
| `bitstamp-public` | `www.bitstamp.net/api/v2/ticker/<x>usd/` | BTC, ETH, SOL, XRP |
| `erapi-public` | `open.er-api.com/v6/latest/USD` | FX rates (USD-EUR …) — second source next to frankfurter-ecb |

No account, key or payment for any of them.

### Canonical crypto instruments

Coinbase, Kraken and Bitstamp all report the **same canonical USD-quoted
instruments** — `BTC`, `ETH`, `SOL`, `XRP` — so one instrument now has
three independent sources and the median consensus
([MESH_MARKET_INTELLIGENCE](MESH_MARKET_INTELLIGENCE.md)) can actually
reject a glitching or lying source. Every payload records the real pair
honestly (`"pair": "BTC-USD"` / `"XBTUSD"` / `"btcusd"`).

Binance keeps reporting its own USDT pairs (`BTCUSDT`, …) as separate
instruments — USDT is not USD, and the store never pretends otherwise.

FX instruments (`USD-EUR`, …) are shared by `frankfurter-ecb` and
`erapi-public` → two-source FX consensus.

## Source health weights (mesh_market_intelligence)

```python
weights = source_weights_from_reports(recent_reports)
verdict = weighted_consensus_price(reports, weights=weights)
```

- `source_weights_from_reports` — per source: weight = `scale_pct` /
  mean absolute deviation (%) from the mesh median, **clamped to
  [0.5, 2.0]**. Sources that consistently land on the median rise toward
  2.0; chronic outliers sink toward 0.5. Bounded, fully reported, and a
  heuristic — not an authority grant.
- `weighted_consensus_price` — same observer/source quorums and same
  inlier band as `consensus_price`; only the final aggregation changes
  from a plain median to a **weighted median of the agreeing inliers**.
  `weights=None` → defers to `consensus_price` unchanged.
- Every verdict reports `weights_used` verbatim — the mesh never hides
  how it weighed a source.

### What weights cannot do

A 2.0-weight source quoting outside the inlier band is still rejected as
an outlier; quorums still decide acceptance. Weights tilt aggregation
among honest reporters — they cannot rescue a liar.

## Verification

18 tests: provider normalization (including Kraken's decorated
`XXBTZUSD` keys and API-error handling), canonical-instrument 3-source
consensus, weight reward/sink behaviour and clamps, weighted-median pull,
inlier-gate supremacy, quorum enforcement. Full suite: 462 passed.
