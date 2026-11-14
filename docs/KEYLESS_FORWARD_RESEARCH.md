# Ghost/keyless forward-data research

Ghost can use genuine public market data without a brokerage account or vendor
API key. Coinbase is the existing default. Kraken is now an explicitly selected
alternative for **BTC-USD and ETH-USD**, using closed one-minute USD spot candles.
There is no automatic source substitution, USD/USDT conversion or synthetic
fallback. Source availability, service terms and redistribution restrictions
remain applicable. Ghost does not make Internet requests anonymous.

## Canonical paper pipeline

The existing authenticated endpoint accepts `"provider":"coinbase"` (default)
or `"provider":"kraken"`. All the same independent risk, freshness, kill-switch,
exact-accounting, idempotency and reconciliation checks remain in force.

```json
{"provider":"kraken","symbol":"BTC-USD","quantity":"0.001"}
```

`OSIRIS_PUBLIC_KRAKEN_ENABLED=false` refuses Kraken before network access or
order mutation. Saved Ghost provider settings also offer Kraken for charts.
Saving preferences does not run the paper strategy or contact an upstream.
Kraken history is bounded to its public 720-minute window and drops the current
uncommitted candle. Product mapping is deliberately limited to the two USD pairs;
unknown assets and quote currencies are refused.

The strategy/candle decision key stays shared across venues. Switching provider
cannot replay a different source as an existing decision or create a second
fill for the same strategy/candle. Existing Coinbase decision fingerprints are
preserved. Ambiguous changes require a new reviewed strategy decision.

Run actual public-data acceptance with an isolated disposable paper account:

```bash
python scripts/run_market_paper_acceptance.py --live-data --provider coinbase \
  --output /tmp/coinbase-paper.json
python scripts/run_market_paper_acceptance.py --live-data --provider kraken \
  --output /tmp/kraken-paper.json
```

The retained result reports the observed outcome, including HOLD, refused SELL,
data gaps and failures. It never invents a favorable signal to force a fill.
The fills remain candle-price simulations; this is not a broker-sandbox match or
a queue/impact/margin calibration. No broker endpoint is contacted.

## Frozen forward-capture archive

`record_keyless_research.py` adds genuine quote recording and research
reproduction alongside the existing synthetic research regression archive.
It reuses the feed-tick validator, walk-forward harness and actual ridge-fitting
model service. It does not add a model deployment or trading path.

Commit the source first. The capture command requires a clean checkout and a
new output directory outside the source tree:

```bash
python scripts/record_keyless_research.py capture \
  --output-dir /tmp/keyless-forward-unique --samples 32 --interval 3
```

Before fetching any data the command freezes `plan.json`: exact candidate SHA,
USD instrument, sample/interval bounds, reference-model parameters, actual fitted
ridge parameters, holdout boundary and the cost grid. The fixed public endpoint
supplies product identity/increments and ticker responses. Requests need no keys.
All original response bytes, SHA-256 digests, request-start and client-receive
times are preserved in a private journal, including duplicate responses and
failed attempts. There is no arbitrary URL input. Public HTTP response size is
bounded before parsing.

Distinct, ordered trade IDs and fresh source timestamps are required. Availability
is the actual client receive time. A historical event time is never substituted
for availability. Duplicate trades are retained in the raw journal but do not
become additional model observations. Errors retain partial evidence and a
failed result; existing output directories are never overwritten.

The completed archive retains original captures and normalized ticks, actual
product mapping, source/runtime hashes, observed quoted-spread diagnostics,
cost-adjusted walk-forward reports at 0/10/25 bps, an actual ridge baseline and
research governance. Ridge features are three returns already known at the
decision; the label is the next observed trade return. One boundary row is
purged so every fitted label arrived strictly before the holdout starts.

The reference walk-forward reports and fitted baseline are reproducible from
the archived data, not profitable or independently validated strategies. A short
forward sample cannot establish reliable statistical performance. Quoted spread
is not realized execution cost. Fixed sensitivity costs are not venue calibration.

The command prints the trusted canonical archive digest. Preserve it separately
and reproduce offline, with matching source/runtime:

```bash
python scripts/record_keyless_research.py verify \
  --archive /tmp/keyless-forward-unique/research-archive.json \
  --commit FULL_CANDIDATE_SHA --sha256 PRINTED_ARCHIVE_SHA256
```

The verifier checks the expected commit/digest, original bytes, source/time
ordering, fixed plan, source hashes and reproduced research results. It makes
no network request. A rehashed report cannot replace reproduced performance or
add an approval. Hashes do not independently authenticate a source or remote run.

Keep original raw data in appropriate private evidence custody. No licence or
redistribution permission is inferred from an endpoint being keyless. Independent
source-rights, model/risk and release review remain distinct requirements. This
feature supplies engineering and genuine-data evidence for T07/T08/T11/T12 and
cost-analysis inputs for T15; it does not mark those gates accepted or waive the
network/security/DR requirements. Live trading remains disabled.
