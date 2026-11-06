# Event-Time Aligned Impact + Significance

> Upgrade over [WORLD_EVENT_IMPACT](WORLD_EVENT_IMPACT.md): instead of
> asking "is the *latest* move unusual?", ask "did the market move
> unusually *after this event's own time*, versus its own baseline just
> before it?" — with an explicit significance statistic.

## Why alignment matters

`assess_event_impact` checks the latest return against a trailing
baseline. If the event happened hours ago, the "latest" window may have
nothing to do with it. `aligned_event_impact` splits every exposed
instrument's return series **at the event's `event_time`**:

- **pre window** — returns stamped strictly before the event = baseline
  (needs ≥ 10 points, `pre_points` cap 40 default)
- **post window** — returns stamped at/after the event = reaction
  (needs ≥ `min_post_points`, default 3; `post_points` cap 10 default)

Per assessed instrument it reports:

| Field | Meaning |
|---|---|
| `post_mean_return` | mean return inside the reaction window |
| `post_cumulative_return` | compounded return over the reaction window |
| `direction` | up / down / flat from the cumulative return |
| `welch_t` | Welch t-statistic, post mean vs pre baseline |
| `significant` | `|welch_t| >= 2` (normal approximation) |

Verdicts: `significant_reaction` / `no_significant_reaction` /
`insufficient_aligned_data` / `no_known_exposure`.

## Honesty contract

- The statistic is a **Welch t with a normal approximation** against the
  instrument's *own* recent baseline — a stricter research lead, not a
  p-value ceremony and **never proof of cause**. Every payload keeps
  `hypothesis: true`, `causality: "not_established"`,
  `live_execution: false`, and states this in `significance_note`.
- Fail-closed: a missing/naive/unparseable `event_time` raises
  `aligned_event_time_missing`; instruments without enough pre/post data
  are reported as `insufficient_aligned_data`, never zero-filled.
- `timestamped_returns_from_store` (new companion) keeps each return
  stamped with the later quote's event_time so the split is exact;
  `market_returns_from_store` behaviour is unchanged.

## Usage

```python
report = aligned_event_impact(store, event_payload, pre_points=40,
                              post_points=10, min_post_points=3)
```

Pairs with the [hypothesis ledger](HYPOTHESIS_LEDGER.md):
`record_from_assessment(store, report)` records every significant
directional finding for later follow-through scoring.
