# P2P cross-host reliability

Tracking issue: #176

## Implementation scope
Retest 2/3 independent hosts at final commit with binary hashes. Add relay/bootstrap outage, partition, reconnect, NAT and soak measurements; preserve raw evidence.

## Acceptance evidence
Commit-specific evidence, peer IDs, digests, latency percentiles, loss, RSS/CPU, restart and recovery.

## Current status
Workstream branch initialized. This document is an execution plan, not evidence of completed implementation. No live trading is enabled.
