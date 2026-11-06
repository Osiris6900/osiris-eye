# Mesh market intelligence — the network is its own source of truth

`osiris_api.mesh_market_intelligence` removes single-provider dependence.
The mesh does not trust Binance, Coinbase or Stooq — it **uses many of
them, plus many independent observers, and keeps only what survives
consensus**. On top of that agreed view it runs its own research:
relationships, lead-lag, regimes and move hypotheses.

## 1. Consensus price view

`consensus_price(reports)` accepts a price only when:

- at least `min_observers` (default 3) independent peers report it, **and**
- at least `min_sources` (default 2) distinct providers are represented, **and**
- a quorum of reports sits within `max_deviation_pct` (default 1%) of the median.

A dead source changes nothing — the median moves on without it. A lying
source (e.g. one report 2.5x off) is rejected as an outlier while the
honest quorum still forms. Disagreement or missing quorum **fails closed**.

## 2. Relationship research

- `returns_series` / `pearson` / `correlation_matrix` — what moves with what
- `lead_lag_scan` — who tends to move *first* (e.g. BTC leading an index)
- `regime_label` — trend (up/down/flat) × volatility (high/normal/low)
- `explain_move` — structured hypothesis for the latest move: z-score
  magnitude, market breadth, leading instruments, current regime

Every output carries `causality: "not_established"` — these are pattern
hypotheses for research, never claims about *why* the market moved.

## 3. Source latency view

`source_latency_report(store)` reads the `fetch_roundtrip_ms` stamps the
global market watch writes into every stored quote payload and ranks each
provider nearest-first: sample count, min/median/last round-trip, newest
sample time, plus a `nearest_sources` top-3.

This is the mesh's answer to "which exchange is closest to us": each node
measures its true HTTP round-trip from wherever it really runs, gossiped
observations carry their *fetching* node's measurement, and the report
aggregates both. As more nodes join from more locations, every exchange
gains a network-near observer. The report is explicitly honest about the
boundary: `co_location: false` — network proximity is measured, physical
exchange co-location is neither claimed nor possible for this software.

## What this is and is not

- The network's view no longer depends on any single provider surviving or
  being honest — that is the independence being built here.
- Prices still originate at real exchanges; a mesh with zero observation
  points sees nothing. Independence comes from **many observers × many
  keyless sources × median consensus**, combined with the signed
  observation contract and E2EE transport.
- Consensus quality scales with observer count. With few observers the
  bounds must be understood as weak evidence.
- Live trading remains disabled; nothing here routes orders.

## Tests

`services/quant-api/tests/test_mesh_market_intelligence.py` — 14 cases —
plus `test_exchange_latency_coverage.py` for the latency report; no network
access, runs in the standard CI Python job.
