# OSIRIS P2P Network Intelligence — Five Capabilities

Offline prototype implementing five **advisory-only** outputs from an operator-supplied JSON snapshot (`schema: osiris.p2p.intelligence.v1`, `peers: [...]`).

1. **Network Brain:** per-peer nominal/degraded heuristic health and sample count.
2. **Smart Routing:** deterministic healthy-first peer ranking, not applied to libp2p.
3. **Anomaly Detection:** high latency, low delivery, repeated disconnects, duplicate spike.
4. **Predictive Failure Detection:** simple degradation early warning, **not** a trained or calibrated prediction.
5. **Autonomous Optimization:** CPU, queue, and peer-health investigation suggestions; **never** automatically applied.

Every peer requires at least 20 reported samples and an authenticated flag; this flag is a **claim**, not cryptographic proof. Existing Rust Noise transport and identity keys must remain intact. No live trading or network mutations.

Run offline: `python scripts/p2p_intelligence_five.py snapshot.json`. Tests: `PYTHONPATH=scripts python -m unittest test_p2p_intelligence_five` from the repository root (depending on test discovery, use `python -m unittest discover -s scripts -p 'test_p2p_intelligence_five.py'`).

Next: instrument actual Rust node peer metrics; bind samples to authenticated Peer IDs and timestamps; benchmark controlled multi-host network conditions; calibrate failure warnings; then evaluate opt-in routing decisions behind rollback and security review. Do not claim self-optimization until that work is verified.
