# Release evidence resync — 2026-11-09

Refs #207, #182. Clean, documentation-only replacement for conflicted PR #304.
This note is an evidence boundary, not a release authorization or a replacement for the machine-readable release-gate inventory.

## Confirmed merged work

- PR #302: Rust SQLite dependency remediation merged. PR-head CI and Dependency Security Audit succeeded. The Rust manifest on main uses rusqlite 0.41 with bundled SQLite; the prior libsqlite3-sys 0.30.1 advisory must not be presented as an unresolved current-main finding.
- PR #291: three-peer bootstrap shutdown/reconnect test merged. Passing PR-head acceptance is not the same as independent-host soak evidence on the final release commit.
- PRs #280 and #283: reconnect backoff and relay dial limits merged; these do not certify 100-peer independent-host reliability.
- PR #286: hashed Python dependency pins included in CycloneDX SBOM.
- PR #284: enrolled-identity read-only registry merged; enrollment inventory is not live peer telemetry.
- PR #290: manual fail-closed release verification gate merged.

## Remaining acceptance evidence

- T01: document residual paste 1.0.15 maintenance warning and verify advisory/license evidence on the exact release candidate.
- T03: retain two- and three-independent-host E2EE runs with per-node binary SHA-256 hashes.
- T04: retain real 100-peer independent-host soak, host fingerprints, latency, loss and outage/recovery evidence. Single-host simulations are not equivalent.
- T06: independent cryptographic and operational review with reviewer, scope and approval.
- T09: licensed vendor market-feed entitlements and redistribution signoff.
- T14–T16: broker sandbox OMS reconciliation, realistic paper accounting, kill-switch and stale-feed fail-closed integration evidence.
- T18–T20: backup/restore, capacity/failover, compliance and model-risk signoffs.

The tracked status of these gates is not changed by this documentation update.
No live trading is authorized. Do not merge an old Cargo.lock, Cargo.toml or generated release inventory merely to refresh prose.
