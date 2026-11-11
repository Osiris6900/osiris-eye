# P2P self-healing: bootstrap recovery health evidence

The node already retries configured bootstrap peers (default 15 seconds) and serves a local `/health` snapshot. This change adds **offline validation of operator-supplied before/after snapshots**. It does not implement a new reconnect algorithm or prove self-healing.

Record `before` during a controlled bootstrap outage, `after` following reconnection, and nonnegative outage/recovery durations. The checker requires the same peer ID, a reported outage followed by bootstrap connectivity, coherent counters and recovery within 120 seconds. It never certifies cross-host delivery, NAT traversal, relay resilience, host independence or production recovery.

Run: `python scripts/check_p2p_self_heal_health.py report.json`

Next: automate capture of timestamped health snapshots on separate authorized hosts, test packet loss and process restarts, verify signed end-to-end observation delivery across the outage, retain immutable artifacts and independently review host identity. **Keep the network's node keys and Noise authentication.** No live trading authorization follows from this evidence.
