# Reproducible research experiment registry

Issue #400; research lineage in #413 and #415. This extends the existing bounded
signal/return backtest; it does not introduce a second backtest implementation.

## Behavior

An experiment stores an immutable ID, requester, UTC creation time, normalized
input arrays, effective parameters, installed source-file SHA-256s, a source
manifest digest, data/input/parameter hashes, Python/platform metadata, the actual
backtest output, its result digest and a receipt digest covering the record.
Signals remain lagged by one observation and turnover costs use the existing
`simulate_signals` contract. There is no fill, margin, borrow or capacity model.

Creation requires the existing research scope and an MFA session. Research roles
can read/export records using existing read authorization; viewers cannot read
research. IDs are global to the configured authority, not tenant-scoped. An
identical retry returns the original immutable record. A change to requester,
inputs, parameters, source or runtime requires another ID. Concurrent identical
requests commit one record; the loser returns that same committed record.

Reproduction executes the same stored inputs only when the installed source and
runtime manifest match, then compares the result digest. Changed source/runtime
is reported as an unreproduced experiment; history is not rewritten. A source
change during execution refuses completion. Reads check the stored receipt,
manifest, input/data/parameter/result hashes, storage version and record ID.

No arbitrary code, shell command, URL, credential or broker input is accepted.
Inputs are bounded to 2–10,000 observations and the existing canonical JSON size
limit. No partial completed record is stored if validation or execution fails.

## API

| Method | Endpoint | Authorization | Result |
|---|---|---|---|
| POST | `/control-plane/experiments` | MFA session, research | Run and persist, or return an identical replay |
| GET | `/control-plane/experiments?limit=100` | research-read | Bounded metadata listing, maximum 1,000 |
| GET | `/control-plane/experiments/{id}` | research-read | Full JSON record for export, or 404 |
| POST | `/control-plane/experiments/{id}/reproduce` | MFA session, research | Re-execute matching source/runtime and compare the result digest |

Creation body:

```json
{
  "id": "research-cost-check-001",
  "payload": {
    "signals": [0, 1, 0],
    "asset_returns": [0, 0.1, -0.1],
    "cost_bps_per_unit_turnover": 10.0
  }
}
```

The response contains `experiment` and `replayed`. The record always has
`live_execution=false` and `deployment_authorized=false`. Omitting the cost
parameter records its effective default of `0.0`. Hashes preserve canonical JSON
values; callers should reuse the exported normalized inputs for exact replay.

The API's existing `OSIRIS_CONTROL_DB`, admin-role and MFA/master-key configuration
applies. No account, order, worker deployment or external integration is created.

## Scope of evidence

Source hashes identify installed files, not a signed release or proof of the
interpreter's loaded instructions. Dataset provenance is explicitly
`caller_supplied_arrays`; recording arrays does not establish licensing,
point-in-time correctness or independent out-of-sample validity. Local hashes
detect ordinary record edits; a privileged actor who rewrites and rehashes the
database requires independently retained evidence to detect. No independent
model approval or strategy deployment authority follows from reproduction.

Tests exercise actual lagged/costed outputs, retries without recomputation,
restart persistence, data/parameter/actor conflicts, source/runtime drift,
mid-run source changes, tampered records and aliases, concurrent creation,
invalid/oversized inputs and authenticated MFA-protected API export/reproduction.
