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

## Operator-attested feed admission

For deployments that require more than a provider allowlist, pass `feed_policy`
to `ingest_p2p_observation` and set `require_feed_policy=True`. A missing policy
then rejects before storage. Each provider policy must explicitly declare:

- an authorized source and entitlement reference;
- `redistribution_rights: "permitted"` (unknown or denied rights fail closed);
- required event/availability timestamps and explicit instrument coverage;
- a passing quality review with reviewer and a timezone-aware, non-stale review time.

The admission report is stored beside the observation. These fields are an
**operator attestation**, not independent verification of a vendor contract,
license, or reviewer identity. Production acceptance still requires the actual
rights and independent quality evidence.

## Paper intents (still not live trading)

`paper_intent_from_observation` requires a second explicit opt-in
(`OSIRIS_PAPER_INTENT_ENABLED=1`) and passes every intent through the
independent risk gate (`osiris_api.risk.check_order`). It only ever produces a
paper intent: `live_execution` is always `False` and no broker or exchange is
contacted.

## Stored observation to durable paper workflow

`osiris_api.paper_observation_bridge.evaluate_stored_observation` closes the
integration gap between an ingested market observation and the existing
backtest/risk paper workflow. The API equivalent is
`POST /paper/workflow/from-observation`.

It loads only an exact record already stored in `p2p_observations`, then
rechecks the second paper opt-in, current provider allowlist, freshness, payload
hash, provenance and a positive unambiguous reference price. Caller-supplied
signal and return histories are evaluated by the existing lagged backtest and
independent risk gate. The observation itself is never treated as a profitable
signal.

Approved, risk-blocked and HOLD evaluations are durably recorded in
`paper_order_intents`. A content-derived intent ID makes an identical retry an
`idempotent_replay` instead of a duplicate downstream record. Set
`require_feed_admission=True` to additionally require a stored admitted-feed
report. No fill, portfolio position, broker submission or live execution is
created.

Example request fields: `record_key`, `source`, `allowed_providers`, `signals`,
`asset_returns`, `quantity`, and optional position/PnL/freshness controls. The
normal API bearer-token middleware applies.

## Tests

`test_p2p_ingestion.py`, `test_feed_admission.py` and
`test_paper_observation_bridge.py` run in the standard CI Python job. They cover
admission failures, current authorization/freshness, risk blocks, HOLD, durable
storage, exact lookups and idempotent replay.

## Honest limits

- This wires *authorized* research/paper ingestion only. Live order routing
  remains disabled by design.
- The P2P node authenticates peers at the transport layer; the provider
  allowlist here is an application-layer provenance gate, not a replacement
  for transport authentication or E2EE.
- Multi-host ingestion evidence (real machines) is still pending under the
  release gates; this module's tests are process-local.
