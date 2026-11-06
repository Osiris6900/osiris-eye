# Autonomous Research Cycle

> The mesh reviews its own store: recent world/celestial events →
> event-time aligned impact → hypothesis ledger → per-rule trust labels.
> One local pass, no network, no broadcast — the node checks itself.

## Why

The watch layers fill the immutable observation store every cycle; the
aligned-impact and ledger layers can measure it. Until now an operator
had to call each piece by hand. The research cycle closes that loop
autonomously on each node, so every significant reaction near a real
event is recorded as a hypothesis and later judged by what the market
actually did — without any human in the loop.

## What one pass does

`run_research_cycle(store, now=...)`:

1. **Scan** — `recent_events_from_store` reads the observations dataset
   newest-first and keeps world/celestial rows (`EQ:`, `EONET:`, `WX:`,
   `ASTRO:`, `SWPC:` record-key prefixes) whose payload is an event with
   a kind and an aware `event_time` inside the lookback window (default
   72 h, max ~1 month). Market quotes are never scanned; repeated
   observations of the same event collapse to the newest; at most
   `max_events` (default 24, max 100) per pass.
2. **Assess** — each event goes through `aligned_event_impact`:
   pre-event baseline vs post-event reaction, Welch t-statistic,
   `significant_reaction` / `no_significant_reaction` /
   `insufficient_aligned_data` / `no_known_exposure` verdicts.
3. **Record** — significant directional findings append to the
   hypothesis ledger (rule label = event kind, created = the event's own
   time). Ids are deterministic, so re-running a cycle records nothing
   twice.
4. **Evaluate** — hypotheses whose horizon has passed are measured
   against the random-walk band (`pre_std × √n`); outcomes append as
   immutable `HYPO:<id>:RESULT` rows (`followed` / `reversed` / `flat` /
   `expired_insufficient_baseline`).
5. **Report** — verdict tallies, hypotheses recorded, evaluation counts,
   outcome tallies, per-rule hit rates and trust labels (`promising` /
   `noise` / `mixed` / `insufficient_samples`).

Everything is read/append-only against the node's own store. Fail-closed:
malformed rows are skipped (never fatal), all bounds validated, and an
event the exposure map cannot price is an honest `no_known_exposure`.

## Honesty contract

A `significant_reaction` is a research lead; a `followed` outcome is
follow-through of a past pattern. Neither is proof of cause, neither is
a guarantee — every report carries `hypothesis: true`,
`causality: "not_established"` and `live_execution: false`.

## Verification

14 tests: event scanning (market rows filtered, lookback + future-skew
bounds, dedupe, max-events bound, validation), empty-store exact report,
significant/quiet/no-exposure events, invalid-event skip, idempotent
re-runs, trust-label surfacing, honesty flags, cycle validation.
