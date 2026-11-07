# Spirit Fleet — many spirits per exchange, standing watch 24x7

One stream session is one listener. The **stream fleet** runs several
independent sessions per venue — the *spirits* — each with its own
connection, its own stats, and its own reconnect budget, so one dropped
wire never silences a venue. Sessions are bounded; the fleet loops them
(`cycles`), so the spirits respawn and stand watch around the clock.

## What a spirit honestly is

A spirit is **one persistent public WebSocket connection through the
venue's own public doorway** — the same documented feed every trading
terminal in the world uses. While the connection lives, the venue pushes
ticks to the spirit the moment the venue publishes them. That is the
honest meaning of "living inside the exchange's data centre": the wire
ends there because the venue itself opened the door.

What a spirit is **not**:

- **Not inside private systems.** Nothing is entered, escalated, or
  disguised. Every byte arrives through endpoints the venues publish for
  everyone.
- **Not faster than co-location.** The venue publishes the tick, then
  the spirits receive it. Co-located trading systems read the matching
  engine first, in microseconds. Push beats *polling*; it does not beat
  physics. `co_location` is `False` in every fleet result, deliberately.
- **Not a crowd at the door.** `spirits_per_venue` is capped at **4**.
  Venue limits sit orders of magnitude higher (e.g. Binance allows 300
  connection attempts per 5 minutes per IP), so 2–4 spirits per venue is
  far inside the rules. Spirit starts are staggered
  (`spirit_stagger_seconds`, default 0.25 s) so the fleet never knocks on
  every door in the same instant.

## Redundancy is measured, not assumed

Two spirits receiving the same tick produce the same signed payload; the
second one is counted in `duplicates_skipped` by the store's hash
dedupe. That count is not waste — it is the *proof* the spirits agree.
A venue where spirits systematically disagree is a venue to distrust.

## The 24x7 loop

Each cycle opens `spirits_per_venue` sessions per watched venue, all
concurrent via `asyncio.gather`, all feeding the same fail-closed
ingestion gates as every other collector (kill switch
`OSIRIS_P2P_INGEST_ENABLED=1`, provider allowlist, freshness, hash
dedupe). When a cycle ends — frame budget, duration, inactivity — the
next one opens fresh connections. A cycle that explodes is recorded in
`cycle_errors` and the fleet walks on; `max_consecutive_errors`
blow-ups in a row stop it honestly (`stop_reason:
"consecutive_errors"`). `cycles` (1…100 000) bounds the duty: for truly
continuous watch, run the fleet under a supervisor — the same discipline
as the roaming and stream watches.

A venue counts as **failed** in the coverage report only when *every*
one of its spirits ended the session errored; a venue with one living
spirit is degraded, not blind.

## Scaling beyond one node

The honest path to "many spirits in every exchange on earth" is the
mesh: each node runs its own fleet (up to 4 spirits per venue), and
signed observations gossip between nodes. Ten nodes × 4 spirits is forty
independent wires per venue — without any single address ever knocking
harder than a polite guest.

## Usage

```python
from osiris_api.data_store import ResearchStore
from osiris_api.stream_fleet import run_stream_fleet

store = ResearchStore("fleet.sqlite3")
result = run_stream_fleet(
    store,
    stream_seconds=3600.0,     # each session lives an hour
    cycles=24,                 # ...and respawns 24 times: a day of watch
    spirits_per_venue=2,       # two independent wires per venue
    max_messages=50_000,
    emit_interval_seconds=1.0,
    inactivity_seconds=30.0,
)
print(result["totals"], result["coverage"]["per_source"])
```

Install the stream extra first (`pip install osiris-quant-api[streams]`)
and enable ingestion (`OSIRIS_P2P_INGEST_ENABLED=1`) or every quote is
surfaced-then-rejected, exactly as the gates demand.

## Result shape

```jsonc
{
  "cycle": "stream_fleet",
  "cycles_run": 24,
  "stop_reason": "cycles_completed",   // or "consecutive_errors"
  "spirits_per_venue": 2,
  "spirits_total": 8,
  "last_session_spirits": { "binance-public#spirit-0": { "/* session stats */": "…" } },
  "totals": { "connections": 0, "ingested": 0, "duplicates_skipped": 0, "…": "…" },
  "coverage": { "sources_ok": 4, "failed_sources": [], "…": "…" },
  "push_stream": true,
  "co_location": false,                // never claimed
  "live_execution": false,
  "broker_order_submitted": false
}
```

## Venues and India

The fleet rides the same four keyless venues as the stream watch
(Binance, OKX, Bybit, Coinbase). For India: NSE and BSE offer **no
official free public feed**, so no spirit can honestly live there —
anything claiming otherwise is scraping someone else's terms. WazirX
publishes a keyless REST ticker (`GET /sapi/v1/tickers/24hr`, INR
pairs); a polling collector for it is the honest India follow-up, after
which WazirX becomes a fleet venue like any other.

## Tests

`tests/test_stream_fleet.py` — 27 tests, no network: scripted sockets
and a fake clock drive every session. Multi-spirit dedupe, cycle
respawning, stagger, dead-venue containment, error-budget stop,
kill-switch gating, and every bound.
