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
- A failed door **cools down** (default 30 s, max 300 s) and is then
  re-probed: blocks lift, and the watcher comes back to check.
- At most `MAX_ENDPOINT_ATTEMPTS` (3) doors per request — failover never
  becomes a hammer.

## 2. Fastest-path ordering

Every attempt is timed; healthy doors are ranked by smoothed round-trip
(EWMA, α=0.3). Known-good doors first (fastest first), never-tried doors
next, cooled-down doors last. Each node continuously re-selects its own
fastest public path — combined with the per-quote `fetch_roundtrip_ms`
telemetry (#136) and `source_latency_report`, the mesh always knows which
door and which node is nearest to which exchange.

`roam.stats()` exposes everything: attempts, successes, failures,
smoothed RTT, cooldown state. No hidden state.

## 3. Coverage truth (`coverage_report`)

Per cycle, from what actually arrived (never assumed):

- per watched source: quotes delivered, status (`ok` / `error` / `silent`)
- `failed_sources` (errored) and `silent_sources` (no quotes, no error —
  suspicious)
- `instrument_sources`: which providers delivered each instrument
- `single_source_instruments`: instruments down to one lifeline

## 4. The 24x7 loop (`run_roaming_watch`)

```python
from osiris_api.data_store import ResearchStore
from osiris_api.roaming_watch import run_roaming_watch

summary = run_roaming_watch(ResearchStore(), cycles=1440, interval_seconds=60.0)
```

- Every cycle runs the full fail-closed watch+ingest pipeline through a
  `RoamingTransport`.
- A cycle that explodes is recorded and the loop walks on — one bad minute
  never kills the watcher. `max_consecutive_errors` (default 12) total
  blow-ups in a row stop it honestly (`stop_reason: consecutive_errors`):
  a node with no working path surfaces that instead of spinning silently.
- Bounds: interval 15–3600 s (consistent with the Rust bridge), cycles
  1–100,000. For truly continuous duty run under a supervisor or the Rust
  market-watch bridge; the ingestion kill switch gates every quote in
  every cycle.

## What "unstoppable" actually is here

| Threat | Answer |
| --- | --- |
| One API domain unreachable | Official alternate domain (failover + cooldown + re-probe) |
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

`services/quant-api/tests/test_roaming_watch.py` — 18 cases, scripted
clock and recording transports, no network access; runs in the standard CI
Python job.
