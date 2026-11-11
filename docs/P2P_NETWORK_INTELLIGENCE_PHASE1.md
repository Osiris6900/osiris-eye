# P2P Network Intelligence — Phase 1

**Scope:** deterministic offline Smart Routing *advice* and Anomaly Detection, not a live route controller or machine-learning predictor.

Input: a bounded JSON snapshot with schema `osiris.p2p_network_intelligence.v1` and a `peers` array. Each peer has a distinct `peer_id`, nonnegative integer latency in milliseconds, delivery success in basis points (0–10000), disconnect and duplicate counts in a 10-minute window, and an `authenticated` claim.

The engine rejects malformed or unauthenticated claimed peers; flags low delivery success (<95%), frequent disconnects (>=5), high duplicates (>=100), or latency (>=2000 ms); and ranks healthy peers first using a deterministic, **uncalibrated** advisory score. No network state is modified.

Run: `python scripts/p2p_network_intelligence.py snapshot.json`

Important boundaries: inputs are not independently authenticated; this is not proof of malicious peers, future failure, or route quality. No identity keys are removed; Noise transport and operator allowlists remain in charge. Next phase: capture actual signed/attributed multi-host metrics from Rust, record time windows and sample sizes, test routing decisions in simulation, then consider a guarded opt-in routing controller after measured improvements and independent security review. No live trading.
