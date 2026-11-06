# Hypothesis Feedback Ledger

> The mesh checks its own predictions. Every unusual-move hypothesis can
> be recorded; once its evaluation horizon has passed, the ledger measures
> what the market actually did and builds per-rule hit-rate statistics —
> so chronically wrong rules get labelled `noise` and stop being trusted.

## Why

The network generates hypotheses every cycle (event impacts, aligned
reactions, `explain_move` flags). Without feedback there is no way to
know whether a rule — "amavasya touches ^SPX", "M-class flares touch
^NDQ" — has *ever* been right. The ledger closes that loop locally on
each node.

## Model

- **Recording** — `record_hypothesis(store, rule_label, instrument,
  direction, created, horizon_points=10)` writes one immutable row into
  the node's own `osiris.hypothesis_ledger` dataset (node bookkeeping,
  not a mesh observation — it does not pass through the p2p ingestion
  gates). The id is a deterministic content hash → recording the same
  hypothesis twice is an idempotent `duplicate`.
  `record_from_assessment(store, assessment)` records every
  unusual/significant directional finding from
  `assess_event_impact` / `aligned_event_impact` automatically.
- **Evaluation** — `evaluate_due_hypotheses(store, now)` finds pending
  hypotheses with enough post-creation market data (≥ horizon new return
  points) and measures the forward cumulative return against a
  random-walk expectation band (`pre_std × √n`, baseline ≥ 10 pre-event
  returns):
  - `followed` — market kept moving the flagged way beyond the band
  - `reversed` — market moved the opposite way beyond the band
  - `flat` — stayed inside the band
  - `expired_insufficient_baseline` — not enough pre-event data; honest
    expiry, never forced
  Outcomes append as immutable `HYPO:<id>:RESULT` rows; re-running is a
  no-op for already-evaluated hypotheses.
- **Hit rates** — `rule_hit_rates(store)` aggregates per rule:
  `hit_rate = followed / (followed + reversed)` and a trust label:
  - `promising` — hit_rate ≥ 0.60 with ≥ 5 scored outcomes
  - `noise` — hit_rate < 0.35 with ≥ 5 scored outcomes
  - `mixed` — in between
  - `insufficient_samples` — not enough scored outcomes yet

## Honesty contract

A `followed` outcome is **follow-through of a past pattern — never proof
of cause, never a guarantee**. The ledger's job is to stop the network
from trusting rules that measurably never work, and to surface the ones
that historically did — as statistics, with `live_execution: false`
everywhere.

## Verification

14 tests: idempotent recording, validation, followed/reversed/flat
outcomes, still-pending and expired paths, idempotent evaluation,
promising/noise/insufficient trust labels, dataset isolation.
