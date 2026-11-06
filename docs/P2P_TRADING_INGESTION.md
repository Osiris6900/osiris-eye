# P2P trading ingestion (issue #87)

`osiris_api.p2p_ingestion` maps signed P2P observations (`osiris.observation.v1`)
from the mesh into the research observation store, with an optional risk-gated
paper-order intent on top.

## Fail-closed gates (all must pass before storage)

1. **Kill switch** — `OSIRIS_P2P_INGEST_ENABLED=1` is required; unset means refuse.
2. **Provenance** — the observation `provider` must be on the operator-supplied
   allowlist; an empty allowlist rejects everything.
3. **Contract** — schema must be exactly `osiris.observation.v1`; `observed_at`
   must be ISO-8601 with timezone; the payload must match its declared
   `payload_sha256` and stay under 16 KiB.
4. **Freshness** — observations older than `max_age_seconds` (default 300s) or
   future-dated beyond a 60s skew bound are rejected. Minor future skew is
   clamped so `available_at` never precedes `event_time` in the store.
5. **Immutability** — the store rejects duplicate `(dataset, source, record_key)`
   keys; re-delivered copies of the same observation fail closed instead of
   double-recording.

## Paper intents (still not live trading)

`paper_intent_from_observation` requires a second explicit opt-in
(`OSIRIS_PAPER_INTENT_ENABLED=1`) and passes every intent through the
independent risk gate (`osiris_api.risk.check_order`). It only ever produces a
paper intent: `live_execution` is always `False` and no broker or exchange is
contacted.

## Tests

`services/quant-api/tests/test_p2p_ingestion.py` runs in the standard CI
Python job and covers the happy path plus every rejection gate.

## Honest limits

- This wires *authorized* research/paper ingestion only. Live order routing
  remains disabled by design.
- The P2P node authenticates peers at the transport layer; the provider
  allowlist here is an application-layer provenance gate, not a replacement
  for transport authentication or E2EE.
- Multi-host ingestion evidence (real machines) is still pending under the
  release gates; this module's tests are process-local.
