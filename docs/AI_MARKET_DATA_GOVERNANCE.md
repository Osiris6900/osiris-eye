# Authorized market data and research governance (#178)

This document defines how external market data reaches AI research, and how
research models are evaluated and governed. It is a design and evidence
reference, not a claim of vendor licensing, profitability, or production
readiness. No live order routing exists in this path.

## Pipeline

```
FeedAdapter (registry-bound)
  -> entitlement admission (feed_admission.admit_feed)
  -> point-in-time tick gate (validate_feed_tick)
  -> admitted ticks only
  -> walk-forward harness (leakage-safe, costs, out-of-sample only)
  -> governance record (research_only)
```

## Authorized adapter interface

`osiris_api.authorized_feeds.ADAPTER_ENTITLEMENTS` is the code-reviewed
registry. An adapter name absent from the registry is refused **before** its
`fetch()` is called — unentitled data is never read. Each registry record is
validated by the existing operator policy (`admit_feed`): authorized source,
entitlement reference, redistribution rights `permitted`, availability
timestamps required, and a fresh passed quality review (30-day default,
fail-closed on expiry).

The sandbox ships only `SyntheticAuthorizedAdapter`
(`license_class: synthetic_test_data`), a deterministic seeded random-walk
fixture with deliberate bad-tick injection. It is not a vendor adapter and
establishes no data rights.

## Point-in-time tick contract (`osiris.feed_tick.v1`)

Every tick carries `event_time`, `available_time`, `received_time` (ISO-8601
with timezone) and a positive finite Decimal price. The gate rejects:

- time-order violations (`event > available` or `available > received`)
- ticks available more than 60s in the future (not yet knowable)
- stale ticks (`received - available` > 300s default bound)
- malformed/missing fields, unsupported schema, bad price
- symbols outside the requested instrument set

`available_time` is the anti-lookahead anchor: research may only use data
after it was available, not after it merely happened.

## Walk-forward research harness

`osiris_api.walk_forward.run_walk_forward`:

- Anchored folds: train on the first `train_min` visible points, score the
  next `test_size` decisions strictly out of sample, then roll by `step`.
- No lookahead by construction: decision `i` receives only ticks with
  `available_time` at or before the decision; exposure decided at `i-1` is
  applied to the return ending at `i`. A defense-in-depth invariant raises if
  any training tick became available after the first test tick.
- Costs: turnover times `cost_bps_per_unit_turnover`, deducted per decision.
- Out-of-sample only: in-sample metrics are never reported
  (`in_sample_metrics: not_reported`). Execution is not simulated.
- Determinism: `data_sha256` (canonical admitted-tick hash) and
  `report_sha256`; identical inputs reproduce identical reports.
- Drift: 10-bin population stability index between the initial training
  returns and the last fold's returns; flagged above PSI 0.25.

## Model governance

`governance_record` binds a model id/version and params to a validated
walk-forward report (`data_sha256` + `report_sha256`). Status is always
`research_only`, `approvals` starts empty, and `live_trading_authorized` is
`false`. Promotion to any live or paper-execution role requires a separate,
explicitly reviewed decision — it is never a consequence of this record.

## Reproduce

```bash
cd services/quant-api
python -m pytest tests/test_authorized_feeds_178.py \
  tests/test_walk_forward_178.py \
  tests/test_market_data_research_gate_178.py -q
cd ../..
python scripts/test_check_market_data_contract.py
python scripts/check_market_data_contract.py <ticks.json>  # offline JSON gate
```

## Boundaries

- Entitlement is an operator attestation, not independent license
  verification. Real vendor integration requires a reviewed registry entry
  and legal clearance.
- Walk-forward metrics are research diagnostics on point-in-time data; they
  exclude fills, market impact, funding, borrow and capacity, and establish
  no return expectation.
- This path enables no live trading and no production deployment.
