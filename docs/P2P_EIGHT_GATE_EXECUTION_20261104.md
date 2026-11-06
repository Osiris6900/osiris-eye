# P2P eight-gate release evidence tracker (2026-11-04)

Status: **NOT RELEASE READY**. This checklist is an operator/reviewer tracking artifact, not proof of completion. Never promote a gate without evidence tied to an exact commit SHA, CI run, independent hosts where required, and reviewer approval.

| Gate | Required acceptance evidence | Current state |
|---|---|---|
| 1. Independent-host E2EE | Two- and three-independent-host delivery, recipient-only exact payload digest, attempt-bound sanitized artifacts | NOT VERIFIED |
| 2. Cross-network relay | Distinct networks, direct dialing blocked, relay delivery, expiry/reconnect/quota denial | NOT VERIFIED |
| 3. Public discovery/bootstrap | Abuse-resistant bootstrap, identity validation, bounded peer state, documented trust boundaries and public reachability from multiple vantage points | NOT VERIFIED |
| 4. Key lifecycle | Rotation/revocation across restart and partitions, fail-closed replay recovery, single-writer durable state | NOT VERIFIED |
| 5. Scale/performance | Reproducible 10/100/1000 peer runs where feasible; latency, throughput, CPU, RAM, churn, fan-out; distinguish simulated and multi-host | NOT VERIFIED |
| 6. Fault recovery | Disconnect, relay/bootstrap outage, partition, stale peers, duplicate delivery; bounded reconnect and resource use | NOT VERIFIED |
| 7. Security/dependencies | Independent crypto review, resolved findings, Rust/Python advisory scans, licenses/SBOM, pinned build and CI action revisions | NOT VERIFIED |
| 8. Public operations | Monitored endpoints, alerting, documented rollback/recovery, sustained uptime observation and signed operator acceptance | NOT VERIFIED |

## Evidence template for each gate

- Gate number and owner:
- Commit SHA / branch:
- Test command / workflow URL / run attempt:
- Host identities and independent-network evidence (sanitized):
- Expected vs actual results, failure cases:
- Artifacts, timestamps and retention location:
- Security reviewer sign-off:
- Decision: PASS / FAIL / BLOCKED (default BLOCKED):

## Dependencies and safe order

1. Review existing open PRs (including #94, #97 and #98), verify CI against their exact heads, and merge only after review. Their passing unit tests do **not** prove public-network acceptance.
2. Establish host/network resources and operator-approved credentials without committing secrets.
3. Execute gates 1–4, then 5–6, then independent gate 7 and operational gate 8. Retest any changes after review.
4. Keep live broker order routing disabled. Do not close existing issues solely because this tracker exists.

Reference: docs/P2P_IMPLEMENTATION_REPORT.md, docs/P2P_DEPENDENCY_REVIEW.md and docs/P2P_RELEASE_GATES.md.

## Execution kickoff: 2026-11-04

All eight gates remain **BLOCKED until independently verifiable evidence exists**. Workstreams can proceed concurrently; do not equate merged tooling with a passed release gate.

| Gate | Immediate executable task | Required external dependency |
|---|---|---|
| 1 | Run 2/3-host E2EE test and record exact commit, recipient digests and redacted artifacts | Two/three independent machines and approved credentials |
| 2 | Exercise relay-only route with direct dial disabled; test expiry, reconnect and quota denial | Distinct network egress and reachable relay |
| 3 | Collect multi-vantage TCP evidence, then verify authenticated peer identity separately | Public bootstrap endpoint and independent vantage networks |
| 4 | Exercise key rotation/revocation across restart and partitions; verify replay-cache single writer | Persistent isolated peer state and controlled test identities |
| 5 | Produce reproducible 10/100/1000-peer load profile with resource metrics | Approved load infrastructure; label simulated runs clearly |
| 6 | Collect sampled outage/recovery probes and run fail-closed offline analyzer | Real outage injection and timestamped host samples |
| 7 | Run Rust/Python dependency audits, produce SBOM, review crypto and licenses | Independent security reviewer and approved audit process |
| 8 | Define alert thresholds, rollback drill, and sustained uptime observation | Operator-approved production-like infrastructure |

### Evidence intake and merge policy

For each gate, capture: owner, commit SHA, workflow run ID, host/network topology (sanitized), expected and observed results, timestamped artifacts, reviewer sign-off and explicit PASS/FAIL/BLOCKED decision. No credentials or private keys in git. Any failed check blocks merge; keep the optional libsodium pilot disabled until the committed Cargo.lock is updated and `cargo test --locked` passes.
