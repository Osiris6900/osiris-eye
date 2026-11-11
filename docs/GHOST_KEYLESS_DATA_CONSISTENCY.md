# Ghost Mode — API-keyless P2P data consistency

**Goal:** Read publicly available market trades without exchange API credentials, compare independently reported prices, and emit a conservative consistency verdict before *paper-only* research.

The existing `services/p2p-node/src/ghost_sniffer.rs` consumes a public Binance WebSocket without an API token. The core P2P node still uses persistent `OSIRIS_NODE_KEY`, Noise-authenticated transport, and Peer IDs. **Never remove these cryptographic identities.** API-keyless does not mean identity-keyless or anonymous/untraceable network activity.

This PR adds `scripts/check_ghost_keyless_consistency.py` as an **offline prototype**, not a running P2P integration. Input is a JSON fixture with two or more distinct reported source names, matching symbols, positive decimal prices, event/receipt timestamps and an authenticated-transport claim. It rejects stale, future, duplicated-source and divergent-price observations. The flag `transport_authenticated` is an input claim and **not independently cryptographically verified** by this script. Source names alone do not establish independent venues or Sybil resistance.

Example: `python scripts/check_ghost_keyless_consistency.py fixture.json --now-ms 1300`

Next integration: bind authenticated peer identity to each observation inside the Rust P2P receive path; map approved public source IDs to observed feed provenance; preserve immutable receipt metadata; compare actual independent venues with symbol/unit normalization; and run multi-host adversarial tests. Keep operator policy and exchange terms in force. Do not route the output to live trading. A consistent result is not a price guarantee or market truth.
