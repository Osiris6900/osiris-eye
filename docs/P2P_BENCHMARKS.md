# P2P benchmarks (issue #86)

`scripts/p2p_benchmark.py` is a **process-local, simulated** gossip benchmark
harness. It runs deterministic fan-out simulations for 10/100/1000 peers and
reports delivery ratio, hop-count latency proxy, transmissions, duplicates
suppressed, wall/CPU time and peak memory.

## Run

```bash
python scripts/p2p_benchmark.py                                   # 10/100/1000 peers, JSON to stdout
python scripts/p2p_benchmark.py --peers 10,100 --fanout 8 --messages 100 --output bench.json
python scripts/p2p_benchmark.py --churn 0.2                       # add churn pressure
python -m unittest discover -s scripts -p 'test_p2p_benchmark.py' -v   # offline harness tests
```

## What the numbers mean (and do not mean)

- `final_delivery_ratio` — fraction of active peers that received the final
  broadcast within the round cap (64). 1.0 without churn is the expected
  baseline for fan-out >= 2.
- `mean_hops` / `max_hops` — gossip rounds to delivery; a latency *proxy*,
  not wall-clock network latency.
- `total_transmissions` / `duplicates_suppressed` — fan-out load and the
  dedup work a real node performs.
- `wall_seconds`, `cpu_seconds`, `peak_memory_bytes` — cost of the simulation
  itself, useful for tracking harness overhead across runs.
- `send_queue_bounded` — asserts per-node send queues never exceed the
  configured fan-out (bounded resource use).

Every report carries `result_class: "simulated_process_local"`,
`multi_host_evidence: false` and `production_readiness_evidence: false`.
These results do **not** establish real multi-host scale, throughput or
latency claims; that evidence requires the independent-machine release
gates (real hosts, real network).

## CI wiring

The harness tests are offline and CI-safe. Add this step to the `python`
job in `.github/workflows/ci.yml`:

```yaml
      - name: Validate simulated P2P benchmark harness offline
        run: python -m unittest discover -s scripts -p 'test_p2p_benchmark.py' -v
```

A periodic benchmark report (e.g. nightly, artifact-uploaded) can be added
later; keeping it out of the per-push path avoids flaky timing assertions.
