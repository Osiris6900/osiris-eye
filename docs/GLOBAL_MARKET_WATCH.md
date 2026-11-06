# Global market watch — the network observes markets itself

`osiris_api.global_market_watch` lets any node watch world markets **without
any account, API key or payment**. It polls keyless public endpoints,
normalizes every quote into the `osiris.observation.v1` contract and feeds
the authorized ingestion pipeline (issue #87), so observations can flow
into the research store and across the P2P mesh on their own.

## Keyless sources (default watchlist)

| Source | Provider ID | Covers | Latency honesty |
| --- | --- | --- | --- |
| Binance Spot 24h ticker | `binance-public` | BTC, ETH, SOL, BNB, XRP (USDT pairs) | near real-time, public endpoint |
| OKX v5 spot tickers | `okx-public` | BTC, ETH, SOL, XRP (USDT pairs) | near real-time, public endpoint |
| Bybit v5 spot tickers | `bybit-public` | BTC, ETH, SOL, XRP (USDT pairs) | near real-time, public endpoint |
| Coinbase Exchange ticker | `coinbase-public` | BTC, ETH, SOL, XRP (USD pairs) | near real-time, public endpoint |
| Kraken public ticker | `kraken-public` | BTC, ETH, SOL, XRP (USD pairs) | near real-time, public endpoint |
| Bitstamp v2 ticker | `bitstamp-public` | BTC, ETH, SOL, XRP (USD pairs) | near real-time, public endpoint |
| Frankfurter (ECB reference rates) | `frankfurter-ecb` | USD vs EUR/GBP/JPY/INR/CNY/AUD/CAD/CHF | daily ECB fixings, not tick data |
| open.er-api.com | `erapi-public` | USD vs EUR/GBP/JPY/INR/CNY/AUD/CAD/CHF | daily rates, not tick data |
| Stooq quotes | `stooq-public` | ^SPX, ^NDQ, ^DJI, ^NKX indices | delayed (~15 min), best-effort |

No source requires signup. Operators can extend the watchlist; unknown
sources fail closed.

USD-quoted pairs share canonical instruments (`BTC`, `ETH`, `SOL`, `XRP`)
across Coinbase, Kraken and Bitstamp; USDT-quoted pairs share instruments
(`BTCUSDT`, …) across Binance, OKX and Bybit — the same pair on three
exchanges is one instrument with three independent observers, which is what
feeds the mesh consensus. USD and USDT instruments stay separate honestly.

## Fetch-latency telemetry (network-near, not co-location)

Every source fetch is timed with a monotonic clock. The HTTP round-trip in
milliseconds is:

- stamped into each quote payload as `fetch_roundtrip_ms` **inside the
  signed payload** (hash recomputed), so a gossiped observation carries the
  fetching node's distance-to-source with it, and
- summarized per source in the cycle result's `latencies` list (including
  failed sources, marked `ok: false`).

The payload stamp is quantized to 5 ms buckets: sub-bucket timing noise
would otherwise make every fetch a unique payload and defeat the
payload-hash dedupe (`duplicates_skipped`) when the market itself has not
moved. The network-nearness signal lives at tens-to-hundreds of ms, far
above that resolution.

`mesh_market_intelligence.source_latency_report(store)` then ranks all
stored sources nearest-first (min/median/last round-trip per provider).
This is the honest software answer to "get the data from as close to each
exchange as possible": real nodes at real locations measure their true
network distance to every exchange, and as the mesh adds nodes in more
places, each exchange gains an observer that is network-near to it. It is
**not physical exchange co-location** — that requires rented rack space and
exchange agreements, which no software can substitute — and the report says
so (`co_location: false`).

## One watch cycle

```python
from osiris_api.data_store import ResearchStore
from osiris_api.global_market_watch import watch_and_ingest

summary = watch_and_ingest(ResearchStore())   # needs OSIRIS_P2P_INGEST_ENABLED=1
```

`watch_and_ingest` fetches every source, then runs **each quote through the
same fail-closed gates as any P2P observation** (kill switch, provider
allowlist, payload hash, freshness). Identical quotes between cycles are
counted as `duplicates_skipped`, not stored twice.

Collection and ingestion summaries include `coverage`, reporting failed and
silent sources, independent sources per instrument, and expected instruments
with no returned quote. An explicit empty watchlist makes no requests.

## Failure semantics

- A source that is down or returns garbage is recorded in `source_errors`;
  the other sources still ingest. Nothing is ever fabricated — `N/D` rows
  are skipped, missing currencies are skipped.
- The ingestion kill switch blocks the whole cycle; the provider allowlist
  gates each source independently.
- `live_execution` and `broker_order_submitted` are always `False`.

## Honest limits

- These are **best-effort public endpoints**: they can rate-limit, delay or
  change format without notice. FX reference rates are daily; Stooq quotes
  are delayed; only crypto tickers are near-real-time.
- This is research observation, not a licensed consolidated feed and not
  trading-grade market data. Live trading remains disabled.
- The [autonomous P2P bridge](P2P_MARKET_WATCH_BRIDGE.md) writes observations
  to an already running node after successful local ingestion. It now retains
  roaming failover and endpoint measurements across cycles. Running this
  bridge does not establish remote delivery or independent-host acceptance.

## Tests

`services/quant-api/tests/test_global_market_watch.py` and
`test_exchange_latency_coverage.py` run in the standard CI Python job with
a fully mocked transport — no network access needed.
