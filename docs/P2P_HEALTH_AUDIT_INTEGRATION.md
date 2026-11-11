# P2P Health → Intelligence → Audit Integration

This integrates the **existing** Rust node's local `/health` JSON endpoint, the previously merged health intelligence bridge, and the previously merged decision hash-chain prototype.

On an operator-run node host:

```bash
python scripts/p2p_health_audit_pipeline.py --endpoint http://127.0.0.1:9090/health
python -m unittest discover -s scripts -p 'test_p2p_health_audit_pipeline.py'
```

A healthy aggregate report creates an `observe` decision; bootstrap outage or isolation creates an `investigate` decision. Each record binds the digest of the health intelligence evidence to the decision, and verifies an in-memory SHA-256 chain.

**Boundaries:** No new Rust telemetry, real multi-host tests, signed source evidence, durable audit writes, external hash anchoring, per-peer route optimization, authenticated remote delivery proof, or live trading. A single health snapshot cannot support predictive failure scoring. Operator must separately store a trusted tip for later tamper/truncation detection. Production integration requires real Rust per-peer metrics and independent host measurements.
