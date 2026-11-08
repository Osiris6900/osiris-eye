# Security dependency remediation

Tracking issue: #171

## Implementation scope
Inspect Cargo.lock and dependency feature graph for hickory-proto, hickory-resolver, yamux and paste. Confirm reachability. Upgrade compatible versions and add repeatable OSV report. Resolve Python deployment lock and license/provenance before signoff.

## Acceptance evidence
Cargo and Python lock evidence; cargo test and integration suites; fresh OSV scan; CI green.

## Current status
Workstream branch initialized. This document is an execution plan, not evidence of completed implementation. No live trading is enabled.
