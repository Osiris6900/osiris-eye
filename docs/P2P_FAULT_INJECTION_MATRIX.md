# P2P fault injection and recovery matrix (issue #91)

`scripts/p2p_fault_injection.py` is a deterministic, in-process fault
harness. It models the mesh with bounded queues and bounded dedup state,
injects faults, and asserts both recovery behavior and resource bounds.

## Matrix

| Fault | Expected behavior | Simulated evidence |
| --- | --- | --- |
| Abrupt disconnect (no goodbye) | Remaining peers keep exchanging; the dead peer receives nothing | `abrupt_disconnect` |
| Bootstrap outage at join | Node joins via durable peer cache; no duplicate connections after bootstrap returns | `bootstrap_outage_recovery` |
| Relay outage | Failover to next configured relay within bounded attempts | `relay_failover` |
| Repeated reconnect failure | Exponential backoff 1→2→4→8→8s (capped), gives up after 5 attempts; immediate retry once peer returns | `reconnect_backoff` |
| Partial partition | No delivery across the cut; log sync heals exactly-once after rejoin | `partial_partition` |
| Duplicate delivery | Dedup stores a single copy; duplicates counted and suppressed | `duplicate_delivery` |
| Stale peer | Silent peer evicted from peer cache after 3 rounds; broadcasts skip it | `stale_peer_eviction` |
| Revoked peer | Reconnect rejected; messages from the revoked peer dropped (fail closed) | `revoked_peer_fail_closed` |

Resource bounds asserted in every scenario: inbox <= 64 messages, dedup
cache <= 256 entries, backoff <= 8s cap, <= 5 reconnect attempts.

## Run

```bash
python scripts/p2p_fault_injection.py        # JSON matrix, exit 0 if all pass
python -m unittest discover -s scripts -p 'test_p2p_fault_injection.py' -v
```

## Honest limits

This matrix is `simulated_process_local` evidence. It validates the
*expected behavior contract* deterministically, but it does not replace the
process-level tests already in CI (two-process, relay, revocation, E2EE) or
the pending independent-machine release gates. Real multi-host fault
injection (actual process kills, network partitions via firewall rules)
remains release-gate work.

## CI wiring

Add this step to the `python` job in `.github/workflows/ci.yml`:

```yaml
      - name: Validate P2P fault-injection matrix offline
        run: python -m unittest discover -s scripts -p 'test_p2p_fault_injection.py' -v
```
