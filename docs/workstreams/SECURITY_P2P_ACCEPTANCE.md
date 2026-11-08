# Security and P2P acceptance

Tracks #171, #167, #176 and #177. This document is an implementation gate, not a passed test.

- [ ] Inventory exact Cargo/Python dependency versions and advisory reachability; remediate and rerun OSV.
- [ ] Pin CI actions to immutable SHAs; retain verified action provenance.
- [ ] Run 2-host and 3-host E2EE on final candidate; retain commit, binary hashes, recipient digest and sanitized logs.
- [ ] Exercise relay loss, NAT traversal, partitions, reconnect, restarts and key revocation across independent hosts.
- [ ] Record p50/p95/p99 latency, throughput, CPU, RSS, disk, dropped messages and recovery time for declared workloads.
- [ ] Complete independent cryptography review and operational key recovery drill.

Do not claim public-node readiness until evidence and operator approval exist.
