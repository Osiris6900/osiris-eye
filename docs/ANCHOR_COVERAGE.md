# Anchor Coverage — who fetches, who listens, nobody blind

A hundred nodes all calling Binance is a hundred times the same
question. The anchor coordinator ends that: per source, well-placed
nodes **anchor** (fetch), everyone else goes **standby** (listen to
gossip). For a standby node the dream is literally true — it never
touches an API, yet the world's data flows in.

## The trust rule

> **Fresh quotes are proof. Announcements are only hints. Doubt fetches.**

- A peer saying "I cover Binance" earns nothing. A peer's signed quote
  that arrived 20 seconds ago earns this node's standby.
- No fresh data → the node fetches itself. A lying peer can never blind
  the mesh; the worst it can cause is a redundant public request.
- Any ambiguity (peer inside the grace band, no fetch history) resolves
  toward **anchor**. Blindness is the one failure this design refuses.

## Duties

| Duty | Meaning |
|---|---|
| `anchor` | This node fetches the source. |
| `standby` | Fresh peer coverage proven — listen only, zero API calls. |
| `covered_by_peer` | This node *cannot* fetch the source, but a peer provably does. |
| `blind` | No one provably covers it — surfaced loudly, never hidden. |

## Failover and hysteresis

- Peer fresher than `freshness_seconds` (default 60 s) → standby.
- Peer older than `freshness_seconds + grace_seconds` (default +15 s) →
  failover: the node anchors again.
- Inside the grace band, `previous_duties` holds the last duty, so a
  peer hovering near the boundary never makes the node flap. With no
  known history inside the band: **doubt fetches** (`anchor`).

## Usage

```python
from osiris_api.anchor_coordinator import anchor_plan, coverage_announcement

result = anchor_plan(
    store,
    own_fetchable={"binance-public": ("BTCUSDT",), "okx-public": ("BTCUSDT",)},
    watched={"wazirx-public"},          # I can't fetch it; is anyone covering?
    own_record_keys=session_record_keys,  # what I fetched myself this session
    previous_duties=last_plan_duties,     # hysteresis across cycles
)
for provider, entry in result["plan"].items():
    if entry["duty"] == "anchor":
        ...  # fetch it; the mesh is counting on us
    elif entry["duty"] == "standby":
        ...  # listen; a peer is provably on it
```

After a successful fetch, a node may gossip a hint:

```python
hint = coverage_announcement("binance-public", ("BTCUSDT",),
                             measured_latency_ms=42.0)
```

The hint helps peers pick the best anchor — but standby is earned only
by fresh quotes. (Ingestion of announcements needs the provider
`node-anchor-announce` in the operator's allowlist, like any source.)

## Near-location placement guide

Anchoring is cheapest where the venue lives. Measured `peer_latency_ms`
(stamped into every gossiped payload) tells the mesh which node sits
closest — no claims, just measurement.

| Venue | Servers | Best anchor region | Typical one-way |
|---|---|---|---|
| Binance | AWS Tokyo (`ap-northeast-1`) | Tokyo / Singapore | ~2–30 ms |
| OKX | AWS Hong Kong | Hong Kong / Singapore | ~2–20 ms |
| Bybit | AWS Singapore | Singapore | ~2–15 ms |
| Coinbase | AWS US East (`us-east-1`) | N. Virginia | ~2–15 ms |
| NSE/BSE (licensed feeds only) | Mumbai | Mumbai | ~2–10 ms |

One honest rule: **one node per region per venue is enough.** Its fast
copy gossips to everyone; dedupe keeps the freshest. The mesh does not
need fifty nodes in Tokyo — it needs one good one, plus failover.

## What this is not

- Not a speed claim: standby data arrives `freshness + gossip` after the
  venue publishes. Co-location is still microseconds; `co_location:
  false` stays in every result.
- Not access to anything private: anchors use the same public, keyless,
  licensed-for-everyone feeds as every other collector.
- Not trust-on-promises: announcements never earn standby.

## Tests

`tests/test_anchor_coordinator.py` — 22 tests through the real
fail-closed ingestion gates: fresh-quote standby, stale-peer failover,
grace-band hysteresis both ways, own-fetch exclusion, unfetchable
sources (covered vs blind), announcements as hints never proof,
latency extraction, bounds, and the kill switch.
