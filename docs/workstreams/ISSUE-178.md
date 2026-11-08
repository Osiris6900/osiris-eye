# AI research and authorized feeds

Tracking issue: #178

## Implementation scope

Inventory quant-api data contracts, enforce entitlement and event/availability timestamps, integrate licensed feed adapter in sandbox, create deterministic walk-forward tests.

## Acceptance evidence

Provenance, stale-feed rejection, no lookahead, reproducible strategy report.

## Implemented (this branch)

- `services/quant-api/src/osiris_api/authorized_feeds.py` — authorized adapter
  interface. `ADAPTER_ENTITLEMENTS` is the code-reviewed registry; unregistered
  adapters are refused before `fetch()` runs. Every registry record must pass
  the existing operator feed-admission policy (`feed_admission.admit_feed`).
  `validate_feed_tick` enforces the point-in-time contract
  `event_time <= available_time <= received_time`, rejects future-available
  ticks (60s skew bound), stale ticks (received-available delay > 300s
  default), non-positive/non-finite prices and unsupported schemas.
  `SyntheticAuthorizedAdapter` is the sandbox fixture adapter
  (license_class `synthetic_test_data`); it is not a vendor integration.
- `services/quant-api/src/osiris_api/walk_forward.py` — anchored walk-forward
  research harness. Point-in-time visibility by construction (decision i sees
  only ticks available before it), exposure at i-1 applied to the return
  ending at i, turnover costs, out-of-sample-only metrics (in-sample metrics
  are never reported), train-vs-last-fold drift (10-bin PSI, flag > 0.25),
  deterministic `data_sha256`/`report_sha256`, and `governance_record`
  (status `research_only`, approvals empty, live trading never authorized).
- Tests: `tests/test_authorized_feeds_178.py` (10),
  `tests/test_walk_forward_178.py` (12),
  `tests/test_market_data_research_gate_178.py` (2, the cross-workstream
  matrix gate: delayed/malformed/stale/future/bad-price ticks rejected;
  research consumes exactly the admitted set — verified by content hash;
  unentitled adapter refused before any fetch).
- `scripts/check_market_data_contract.py` — standalone offline CI gate for
  JSON tick batches (same ordering/freshness/entitlement rules).

## Acceptance status

All scope items above are implemented with tests on this branch. This
document plus the test suite is the evidence; CI run link is attached in the
PR. No live trading is enabled. No vendor license is claimed; entitlement is
an operator attestation, not independent verification.
