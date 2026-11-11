# Rust P2P /health → Network Intelligence bridge (Phase 2a)

The existing Rust core node exposes `GET http://127.0.0.1:9090/health` on loopback. This bridge queries that **actual endpoint** (when an operator runs it), validates counters and reports bootstrap isolation/partial disconnection. It does not require new Rust instrumentation and it does not read from a fabricated fixture in production.

Example on a running node host:

```bash
python scripts/p2p_health_intelligence_bridge.py --endpoint http://127.0.0.1:9090/health
python -m unittest discover -s scripts -p 'test_p2p_health_intelligence_bridge.py'
```

The bridge only permits numeric loopback HTTP URLs, disables proxies, limits response bytes and time, and never changes P2P routing or trading state. Multiple endpoints are only supported if each is exposed locally (for example by operator-controlled tunnels). Do not expose `/health` to the public Internet for this tool.

**Important limitations:** /health reports aggregate counts, not per-peer latency, delivery rate, CPU, queue, authenticated peer attribution, historical failure patterns or remote observation delivery. Therefore Smart Routing and Predictive Failure explicitly report **unavailable**, and Autonomous Optimization reports only investigation suggestions. A single health response is not evidence of independently hosted nodes. Next: Rust per-peer metric instrumentation, signed/attributed time-series, independent cross-host tests and controlled simulation before enabling any routing action. No live trading authorization.
