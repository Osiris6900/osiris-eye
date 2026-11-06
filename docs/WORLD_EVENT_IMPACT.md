# World Event Watch & Event→Market Impact

> The network watches everything — earthquakes, storms, volcanoes, wildfires,
> ice, city weather — and checks each event's impact on the market.
> Every impact statement is a labelled hypothesis; causality is never claimed.

This layer extends [GLOBAL_MARKET_WATCH](GLOBAL_MARKET_WATCH.md) from market
quotes to **world events**. Two modules:

| Module | Role |
|---|---|
| `osiris_api.world_event_watch` | Polls keyless world-signal sources, normalizes each event into the `osiris.observation.v1` contract, feeds the fail-closed ingestion pipeline (issue #87) |
| `osiris_api.event_market_impact` | Maps each observed event to plausibly exposed instruments and checks whether any of them moved unusually afterwards |

## Sources — all keyless (no account, no key, no payment)

| Provider | Source | What it sees |
|---|---|---|
| `usgs-earthquakes` | USGS GeoJSON feeds (`significant_day`, `4.5_day`, …) | Earthquakes worldwide: magnitude, place, depth, tsunami flag |
| `nasa-eonet` | NASA EONET v3 (`status=open`) | Severe storms, volcanoes, wildfires, sea/lake ice, floods, and more |
| `open-meteo` | Open-Meteo current conditions | Live weather in six financial centres (New York, London, Tokyo, Mumbai, Frankfurt, Singapore) |

`DEFAULT_WORLD_WATCHLIST` covers all three; operators can extend it (feed
names, EONET categories, city slugs are validated fail-closed).

## Contract mapping

Every event becomes a signed observation — same contract as market quotes,
so the existing mesh broadcast, ingestion gates, freshness bounds and
immutable store all apply unchanged:

| Field | Value |
|---|---|
| `instrument` | `EQ:USGS:<id>`, `EONET:<id>`, `WX:<CITY>` |
| `provider` | `usgs-earthquakes` / `nasa-eonet` / `open-meteo` |
| `observed_at` | when the network collected the event |
| `payload.event_time` | when the event itself occurred (never backdated into `observed_at`) |
| `payload.kind` | `earthquake` / `<eonet category>` / `weather` |

Freshness gates therefore mean honestly: "the mesh saw this recently".

## Impact assessment

`assess_event_impact(event, returns_map)`:

1. **Exposure map** (`DEFAULT_EXPOSURE_RULES`, operator-extensible): event
   `kind` + location keywords → instruments, e.g. earthquake mentioning
   "Japan" → `^NKX`, `USD-JPY`; a Florida hurricane → `^SPX`, `^DJI`;
   Tokyo weather → `^NKX`, `USD-JPY`. Instruments deliberately mirror the
   keyless market watchlist so impact can always be checked.
2. **Move check**: each exposed instrument's latest return is scored with
   the existing `mesh_market_intelligence.explain_move` primitive —
   z-score vs its own baseline, direction, breadth, regime.
3. **Verdict**: `unusual_move_detected` / `no_unusual_move` /
   `insufficient_market_data` / `no_known_exposure`.

`market_returns_from_store(store, instruments)` rebuilds per-instrument
return series from the immutable `p2p_observations` dataset (oldest→newest
by `event_time`, capped, instruments with <2 quotes omitted — never
zero-filled).

### Honesty contract

```json
{
  "verdict": "unusual_move_detected",
  "hypothesis": true,
  "causality": "not_established",
  "live_execution": false
}
```

An unusual move near an event is a **research lead**, never proof the event
moved the market. Instruments with no data are reported as
`no_market_data`; nothing is fabricated.

## Usage

```python
from osiris_api.world_event_watch import watch_and_ingest_world
from osiris_api.event_market_impact import (
    assess_event_impact, market_returns_from_store,
)

report = watch_and_ingest_world(store)          # one world-watch cycle
series = market_returns_from_store(store, ["^NKX", "USD-JPY"])
impact = assess_event_impact(event_observation, series)
```

Ingestion still requires the explicit opt-in `OSIRIS_P2P_INGEST_ENABLED=1`
and the provider allowlist — the world watcher cannot bypass a single gate.

## Bounds

| Bound | Value |
|---|---|
| Events per source per cycle | ≤ 100 |
| EONET events per category | ≤ 25 |
| Response size | ≤ 2 MiB |
| HTTP timeout | 1–30 s |
| Exposed instruments per event | ≤ 16 |
| Assessments per event | ≤ 8 |
| Returns points per instrument | 2–512 |

## What this layer does NOT do

- No causality claims (`causality: "not_established"` everywhere)
- No live trading (`live_execution: false`; live execution stays permanently disabled)
- No fabricated events, quotes, magnitudes or moves — unreachable or
  malformed sources are recorded as errors
- No API keys, accounts or paid feeds anywhere in the chain

## Release note

Impact assessment is a local analytics layer over the same observation
store; multi-host agreement on events uses the same median-consensus
machinery as market prices ([MESH_MARKET_INTELLIGENCE](MESH_MARKET_INTELLIGENCE.md)).
