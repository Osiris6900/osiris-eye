# Roaming watch — the watcher that cannot be stopped by one closed door

`osiris_api.roaming_watch` is the "hidden spirit" layer over the global
market watch: a 24x7 self-healing watcher that roams the **public** roads
of every exchange it covers, always through the fastest measured door, and
survives any single failure.

## Honest edges (read first)

- **The spirit roams public roads only.** Every byte comes from endpoints
  the venues themselves publish for everyone, with no key, no signup. This
  layer never enters an exchange's private systems, never disguises itself,
  never rotates identities, and never works around a deliberate block —
  that would be unauthorized access, which this project does not do.
- **"Faster than co-location" is not a claim we make.** Co-located systems
  read the matching engine in microseconds inside the exchange's own data
  centre; no public-internet software can beat that. What this layer *does*
  deliver: the fastest public path available to each node, measured and
  re-chosen continuously, plus uptime and coverage no single co-located
  box can match. Best in resilience and reach; honest about raw speed.
- **Unstoppable means redundant, not aggressive.** One endpoint falls →
  another official door; one exchange falls → other venues quote the same
  instrument; one node falls → the rest of the mesh keeps watching.

## 1. Endpoint failover (`RoamingTransport`)

Wraps any transport. Each request host is mapped to its **officially
documented** alternate domains (`EQUIVALENT_HOSTS` — currently Bybit's
`api.bytick.com`; only venue-published alternates ship here, operators may
add regional ones like Binance.US `api1/api2.binance.us`).

- Only transport-level unavailability (`RuntimeError`) moves to the next
  door. A well-formed but unwanted response (`ValueError`, e.g. oversize)
  is surfaced, not retried — content problems don't fix themselves on
  another host.
- A failed door **cools down** (default 30 s, max 300 s). It receives no
  requests during that time, even when every door is unavailable. If all
  doors are cooling down, the source reports `watch_endpoints_cooling_down`.
  After expiry the door becomes eligible again; it is tried if preferred
  healthy doors fail. Successful recovery clears the cooldown.
- At most `MAX_ENDPOINT_ATTEMPTS` (3) doors per request — failover never
  becomes a hammer.

## 2. Health-scored ordering

Every attempt is timed. Eligible doors (cooldown expired) are ranked by
`endpoint_health_score`, highest first, with declared order breaking ties:

- **Known-good** (at least one success): strictly above 0.5. A higher
  success ratio and a lower smoothed round-trip (EWMA, α=0.3) score higher,
  so the measured-fastest healthy door is still preferred.
- **Never tried**: 0.5.
- **Known-bad** (failures and no success): 0. Failover prefers an untried
  official door over one that has only failed.

Active cooldowns are excluded. This ranks paths already measured by the
node, rather than claiming to discover the globally fastest path. Untried
or recovered doors are not proactively probed while a preferred healthy
door works. Combined with per-quote `fetch_roundtrip_ms` telemetry (#136)
and `source_latency_report`, the mesh always knows which door and which
node is nearest to which exchange.

A **429 or 418** is not a generic outage. The door cools down for the
bounded `Retry-After` (1–300 seconds; missing or unparseable headers use
60 seconds) and the same request does not try a sibling domain — those
official alternates often share one quota. The next cycle can use a door
that is not cooling.

`roam.stats()` exposes everything: attempts, successes, failures, rate
limits, smoothed RTT, cooldown state, and `health_score`. No hidden state.

## 3. Coverage truth (`coverage_report`)

Per cycle, from what actually arrived (never assumed):

- per watched source: quotes delivered, status (`ok` / `error` / `silent`)
- `failed_sources` (errored) and `silent_sources` (no quotes, no error —
  suspicious)
- `instrument_sources`: which providers delivered each instrument
- `single_source_instruments`: instruments down to one lifeline
- `missing_instruments`: expected instruments with no watched source
  returning a quote, including total blind spots; FX and index symbols
  use the collector's canonical instrument names

The shared helper lives in `osiris_api.watch_coverage` and is re-exported
from `roaming_watch` for existing callers. Collection, ingestion and P2P
bridge cycle summaries expose it as `coverage`. Only watched provider and
instrument combinations count. An explicit empty watchlist stays empty.
Coverage describes fetched quotes; it does not certify ingestion or delivery.

## 4. The 24x7 loop (`run_roaming_watch`)

```python
from osiris_api.data_store import ResearchStore
from osiris_api.roaming_watch import run_roaming_watch

summary = run_roaming_watch(ResearchStore(), cycles=1440, interval_seconds=60.0)
```

- Every cycle runs the full fail-closed watch+ingest pipeline through a
  `RoamingTransport`.
- Exceptions and cycles returning **zero watched observations** from a nonempty
  watchlist both increment the failure streak. The latter records
  `watch_no_observations`, whether all sources failed or silently returned
  nothing. `max_consecutive_errors` (default 12) stops the loop with
  `stop_reason: consecutive_errors`; it does not sleep after stopping.
- Any watched observations reset the failure streak, including duplicates or
  ingestion-rejected quotes: source health is distinct from permission to
  store a quote. Useful partial coverage continues as `degraded`; an
  explicit empty watchlist is `idle` and performs no requests.
- `latest_coverage` and `cycle_reports` retain source errors, ingestion
  rejections and blind spots. Reports use zero-based cycle numbers and
  statuses `ok`, `degraded`, `no_observations`, `idle` or `error`. An
  exception has no coverage (`null`). Only the latest 50 reports and errors
  are kept, in memory as well as in the returned summary.
- Bounds: interval 15–3600 s (consistent with the Rust bridge), cycles
  1–100,000. For truly continuous duty run under a supervisor or the Rust
  market-watch bridge; the ingestion kill switch gates every quote in
  every cycle.

## Autonomous P2P bridge

`market_watch_bridge.broadcast_cycle` now uses a `RoamingTransport`, and
`run_bridge` retains one instance across its cycles so endpoint measurements
and cooldowns survive. An explicitly supplied roaming transport is reused.
Each bridge cycle reports `coverage` and `endpoint_stats`. Broadcasting still
requires the bridge switch and successful gated local ingestion; writer
errors propagate. See [P2P bridge](P2P_MARKET_WATCH_BRIDGE.md).

## What "unstoppable" actually is here

| Threat | Answer |
| --- | --- |
| One API domain unreachable | Official alternate domain (failover + cooldown + re-probe) |
| Venue returns 429 or 418 | That door waits out a bounded Retry-After; siblings are not hammered on the same request |
| Door has only failed | Health score ranks it behind never-tried official doors |
| One exchange down/blocking | Other keyless venues quote the same instrument (9 sources, USDT pairs on 3 exchanges, USD pairs on 3 exchanges) |
| One node offline | The rest of the mesh keeps watching and gossiping observations |
| Slow path to a venue | Fastest-path ranking picks the node's measured-fastest door |
| Blind spots | `coverage_report` + `source_latency_report` expose them immediately |

## Roadmap (not built yet)

- **Public WebSocket streams** — the venues' own push feeds are the next
  honest latency step (push vs poll), and the closest legal thing to
  "living inside" the exchange: the exchange itself streams every tick to
  you. Requires a websocket client dependency and an async path; sized as
  follow-up work.

## Tests

Roaming watch, transport cooldown, coverage and market-watch bridge tests
use scripted clocks and transports without network access. They cover total
outages, silence, partial recovery, retained evidence, failover, endpoint state
reuse, rate-limit fault injection, health-score ordering, and ingestion/broadcast
gates in the standard CI Python job.
