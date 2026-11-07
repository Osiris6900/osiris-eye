# Stream watch — the exchanges speak first

`osiris_api.stream_watch` opens the venues' own **public** WebSocket feeds
and listens. The polling watch walks up to each exchange every cycle and
asks; the stream watch is *told* — the exchange pushes every tick the
moment it happens. This is the closest legal thing to "living inside" the
exchange: the venue itself streams its market data to anyone, no key, no
signup.

## Honest edges (read first)

- **Public feeds only.** Every byte arrives on endpoints the venues
  publish for everyone. No authentication is bypassed, no private channel
  is touched, nothing is disguised.
- **Push beats poll; neither beats co-location.** A pushed tick removes
  the poll interval and one round-trip from the path — that is the honest
  gain. Co-located systems still read the matching engine in microseconds
  inside the exchange's own data centre. No claim beyond "fresher than
  our own polling" is made: `co_location: false` in every result.
- **Same gates, same contract.** Streamed quotes become
  `osiris.observation.v1` observations and pass the identical fail-closed
  ingestion pipeline as polled quotes (kill switch, allowlist, freshness,
  payload-hash dedupe). A fast pipe is not a trusted pipe.

Requires the optional dependency: `pip install osiris-quant-api[streams]`
(the module imports and all tests run without it; only real connections
need the package).

## Venues (all keyless)

| Provider | Endpoint | Instruments shared with polling |
| --- | --- | --- |
| `binance-public` | `wss://stream.binance.com:9443/stream?streams=…@miniTicker` | USDT pairs |
| `okx-public` | `wss://ws.okx.com:8443/ws/v5/public` (`tickers` channel) | USDT pairs |
| `bybit-public` | `wss://stream.bybit.com/v5/public/spot` (`tickers.` topics) | USDT pairs |
| `coinbase-public` | `wss://ws-feed.exchange.coinbase.com` (`ticker` channel) | USD pairs |

Same canonical instruments as the polling watch, so streamed quotes join
the same consensus: one `BTCUSDT` observed by three venues, one `BTC` by
Coinbase plus the USD poll observers.

## Usage

```python
from osiris_api.data_store import ResearchStore
from osiris_api.stream_watch import run_stream_watch

# One bounded session: 10 minutes across all four venues.
result = run_stream_watch(ResearchStore(), stream_seconds=600.0)
```

For 24x7 duty, call it in a loop under a supervisor (or alongside the
roaming loop): a session is deliberately finite — duration 5 s–24 h,
per-venue frame budget, bounded reconnects.

## What a session guarantees

- **Duration bound** — `stream_seconds` (5–86,400). Time runs out, the
  session ends. `stop_reason: duration_elapsed`.
- **Frame budget** — `max_messages` per venue (1–1,000,000, default
  10,000). A runaway stream cannot flood the store:
  `stop_reason: frame_budget_spent`.
- **Emission throttle** — `emit_interval_seconds` per instrument
  (0.2–60, default 1.0). The stream may tick 10× a second; the store
  takes at most one quote per instrument per interval. Throttled ticks
  are counted, never silently dropped: `quotes_throttled`.
- **Inactivity watchdog** — no frame for `inactivity_seconds` (5–300,
  default 30) and the connection is declared dead and re-established.
- **Bounded reconnects** — `max_reconnects` (0–10, default 3) with
  exponential backoff (1 s, 3 s, 7 s, … capped at 30 s). A venue that
  cannot stay connected stops honestly:
  `stop_reason: reconnects_exhausted`, with `last_error` recorded — and
  never takes the other venues down with it.
- **Never adopt the unasked** — a quote for an instrument that was not
  requested is parsed, counted, and discarded. The watchlist is the
  contract.

## Provenance in the signed payload

Every streamed quote carries:

- `feed: "websocket_push"` — how it arrived
- `venue_event_age_ms` — receive time minus the venue's own event
  timestamp, quantized to 5 ms buckets (so a quiet market still dedupes
  instead of flooding). Includes node clock skew:
  `clock_skew_note: "age_includes_node_clock_skew"`
- `venue_ts_source: "venue" | "node"` — whose clock stamped the tick;
  when the venue sends no timestamp the node stamps its own and says so

## Coverage

The session result reuses the shared `watch_coverage.coverage_report`:
per-source status (`ok` / `error` / `silent`), `failed_sources`,
`instrument_sources`, and `single_source_instruments` — so a dead stream
shows up exactly like a dead poll source.

## Tests

`services/quant-api/tests/test_stream_watch.py` — 22 cases: per-venue
parsers (including confirmation/heartbeat rejection and bad-price
raises), scripted fake sockets for sessions (throttle, dedupe, kill
switch, reconnect, exhaustion, two-venue concurrency), and bound
validation. No network access.
