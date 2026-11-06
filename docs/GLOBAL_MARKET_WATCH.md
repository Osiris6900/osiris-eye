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
| Frankfurter (ECB reference rates) | `frankfurter-ecb` | USD vs EUR/GBP/JPY/INR/CNY/AUD/CAD/CHF | daily ECB fixings, not tick data |
| Stooq quotes | `stooq-public` | ^SPX, ^NDQ, ^DJI, ^NKX indices | delayed (~15 min), best-effort |

No source requires signup. Operators can extend the watchlist; unknown
sources fail closed.

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
- Wiring the watcher into the Rust node loop (auto-broadcast of each
  observation to the mesh) is follow-up work; today an operator runs the
  cycle and the mesh distributes observations through the existing P2P
  paths.

## Tests

`services/quant-api/tests/test_global_market_watch.py` runs in the standard
CI Python job with a fully mocked transport — no network access needed.
