# P2P market-watch bridge — the node watches the world by itself

`osiris_api.market_watch_bridge` + `scripts/p2p_market_watch_bridge.py`
close the autonomy loop. A node now:

1. **observes** — runs the keyless global market watch cycle on an interval
2. **remembers** — validates and stores each observation through the
   fail-closed local ingestion gates
3. **broadcasts** — writes each accepted observation as a JSON line to the node
   process's stdin (the node's existing ingestion path); the mesh gossips
   it to every peer
4. **researches** — `mesh_market_intelligence` (consensus, relationships,
   regimes) runs over the stored observations

No per-cycle operator action. No account, key or payment for the data.

## Run

```bash
cargo build --release --manifest-path services/p2p-node/Cargo.toml
pip install -e 'services/quant-api[dev]'

export OSIRIS_MARKET_WATCH_BROADCAST=1     # bridge kill switch
export OSIRIS_P2P_INGEST_ENABLED=1         # required local validation and storage
export OSIRIS_BOOTSTRAP_PEERS=/dns4/seed.example/tcp/9001/p2p/<peer-id>
python scripts/p2p_market_watch_bridge.py  # 60s interval, 1440 cycles = 24h
```

## Gates and bounds

- Bridge refuses to run without `OSIRIS_MARKET_WATCH_BROADCAST=1`.
- A research store is required; with the ingestion kill switch off, no
  observations are broadcast. Rejected and duplicate observations are skipped.
- Interval is bounded to 15–3600s; cycle count is bounded (default 1440).
- A source that fails is isolated; the rest of the world still broadcasts.
- Every broadcast observation is the signed `osiris.observation.v1`
  contract, so receiving peers apply the same validation as any mesh
  traffic.

## Endpoint failover and cycle diagnostics

The bridge uses [`RoamingTransport`](ROAMING_WATCH.md) for both standalone
`broadcast_cycle` calls and the `run_bridge` loop. One transport instance
is retained throughout the loop, preserving measured endpoint latency and
failure cooldowns across cycles. An injected `RoamingTransport` is reused
as supplied. Transport failures can fall back to the venue's documented
alternate hosts; response-content errors do not trigger alternate requests.

Each cycle summary includes `source_errors`, `coverage` and `endpoint_stats`,
alongside the accepted, rejected, duplicate and broadcast counts. Coverage
describes the quotes fetched before local ingestion; it does not mean those
quotes passed the broadcast gates. For example, a Bybit primary outage
followed by a successful official alternate produces successful source
coverage while endpoint statistics retain the primary failure. The final
loop summary also exposes cumulative `endpoint_stats`.

## Honest limits

- Interval floor of 15s: this is a **market-watching** cadence, not
  tick-level HFT data.
- One node watching alone is one observer: consensus
  (`mesh_market_intelligence`) needs ≥3 observers and ≥2 sources to accept
  a price. The design assumption is many nodes each running this bridge.
- Live trading remains disabled; nothing here routes orders.
