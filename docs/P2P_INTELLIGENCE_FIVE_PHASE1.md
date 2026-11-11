# P2P Intelligence: five-capability phase-one prototype

`scripts/p2p_intelligence_five.py` accepts offline operator-supplied time-series samples for 1–100 authenticated-*claimed* nodes, with 2–12 ordered observations each. No P2P node integration is present yet.

1. **Network Brain:** summarizes last reported latency, delivery rate and health.
2. **Smart Routing:** deterministic, healthy-first advisory ranking.
3. **Anomaly Detection:** threshold-based flags for poor delivery, latency, disconnects and duplicates.
4. **Predictive Failure:** **uncalibrated trend warnings only**, based on two consecutive samples. No validated failure prediction.
5. **Autonomous Optimization:** generates operator-review recommendations; **no autonomous changes are executed**.

Use `python scripts/p2p_intelligence_five.py snapshot.json`. All numbers and authenticated flags are untrusted until linked to cryptographically attributed Rust-node telemetry. Existing P2P identity keys, Noise and allowlists remain unchanged. This is not a live-routing controller or a live-trading feature.

Next: authenticated telemetry collection, sampling windows, adversarial multi-host tests, baseline-versus-candidate routing simulation, calibration, rollback and operator approval gates.
