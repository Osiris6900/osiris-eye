# P2P eight-gate release evidence tracker

Reviewed 2026-11-06 against implementation baseline
`4e18c70ee85c3457af7eb251501ee3c06d3ddd80`.

Status: **NOT RELEASE READY**. This checklist records implementation support and
remaining acceptance work. Every overall gate stays **BLOCKED** until its evidence
record identifies the exact candidate commit, workflow attempt, relevant independent
hosts/networks and reviewer decision. A merged harness or green workflow alone does
not prove that the required topology or operational conditions were exercised.

| Gate | Required acceptance evidence | Available implementation support | Gate decision |
|---|---|---|---|
| 1. Independent-host E2EE | Two- and three-independent-host delivery, recipient-only exact payload digest, attempt-bound sanitized artifacts | [Two-host](P2P_CROSS_HOST_E2EE_ACCEPTANCE.md) and [three-host](P2P_THREE_HOST_E2EE_ACCEPTANCE.md) harnesses/workflows exist; same-machine process tests remain a separate evidence class | BLOCKED: no complete independent-host evidence record accepted here |
| 2. Cross-network relay | Distinct networks, direct dialing blocked, relay delivery, expiry/reconnect/quota denial | [Core relay harness](../scripts/test_p2p_core_relay_e2ee.py) checks local relayed E2EE with mDNS and DCUtR disabled; [loopback quota tests](../services/p2p-node/tests/relay_quota.rs) assert reservation/circuit denial and client failure; normal node hole punching defaults on | BLOCKED: local relay success and quota tests do not verify cross-network isolation or all failure cases |
| 3. Public discovery/bootstrap | Abuse-resistant bootstrap, identity validation, bounded peer state, documented trust boundaries and public reachability from multiple vantage points | [Discovery implementation](P2P_PUBLIC_DISCOVERY.md), [TCP evidence gate](P2P_EXTERNAL_TCP_EVIDENCE.md) and [bootstrap configuration gate](P2P_NO_CENTRAL_AUTHORITY.md) exist | BLOCKED: declared vantage/operator independence requires external verification; TCP success is not authenticated P2P delivery |
| 4. Key lifecycle | Rotation/revocation across restart and partitions, fail-closed replay recovery, single-writer durable state | [Rotation/revocation runbook](P2P_KEY_ROTATION_REVOCATION_RUNBOOK.md), [durable peer-key storage](P2P_PEER_KEY_STORAGE.md) and local restart/revocation tests exist | BLOCKED: independent-host lifecycle and partition/recovery evidence still required |
| 5. Scale/performance | Reproducible 10/100/1000 peer runs where feasible; latency, throughput, CPU, RAM, churn and fan-out; distinguish simulated and multi-host measurements | [Benchmark harness](P2P_BENCHMARKS.md) runs deterministic 10/100/1000-peer simulations with explicitly process-local metrics | BLOCKED: simulated hop counts and harness resources are not actual network latency, throughput or fleet capacity |
| 6. Fault recovery | Disconnect, relay/bootstrap outage, partition, stale peers and duplicate delivery; bounded reconnect and resource use | [Fault-injection matrix](P2P_FAULT_INJECTION_MATRIX.md), [resilience controls](P2P_RESILIENCE.md), process tests and [sampled outage analyzer](../scripts/check_p2p_outage_recovery.py) exist | BLOCKED: simulated contracts and offline analysis do not replace measured multi-host faults and recovery |
| 7. Security/dependencies | Independent crypto review, resolved findings, Rust/Python advisory scans, licenses/SBOM, pinned build and CI action revisions | [Dependency inventory](P2P_DEPENDENCY_REVIEW.md) and internal adversarial tests exist; locked Rust builds are distinct from a fully reproducible deployment environment | BLOCKED: inventory/internal review is not an independent audit, current advisory scan or complete license/SBOM clearance |
| 8. Public operations | Monitored endpoints, alerting, documented rollback/recovery, sustained uptime observation and signed operator acceptance | [Deployment profile](P2P_HARDENED_DEPLOYMENT.md) and [public-node procedure](P2P_PUBLIC_NODE_DEPLOYMENT.md) exist | BLOCKED: configuration does not establish installation, sustained uptime, monitoring response or operator acceptance |

The [2026-11-04 execution kickoff](P2P_EIGHT_GATE_EXECUTION_20261104.md)
preserves the original task order and historical status. This tracker adds the
implementation support delivered since that kickoff; it does not retroactively
mark any release gate as passed.

## Evidence template for each gate

- Gate number and owner:
- Candidate commit SHA / branch; binary hashes and clean/dirty source status:
- Test command / workflow URL / run ID and attempt:
- Evidence class: simulated process-local / single-host process integration / independent-host / public-network:
- Host identities, network topology and independently verified network ownership/egress (sanitized):
- Expected vs actual results, failure cases:
- Artifacts, UTC timestamps, retention location and expiry:
- Security reviewer sign-off:
- Decision: PASS / FAIL / BLOCKED (default BLOCKED):

## Dependencies and safe order

1. Review the current candidate and its dependencies, verify CI against the exact head, and retain the candidate's evidence before promotion. Earlier PR numbers or historical green runs are not evidence for a later revision.
2. Establish host/network resources and operator-approved credentials without committing secrets.
3. Execute gates 1–4, then 5–6, then independent gate 7 and operational gate 8. Retest any changes after review.
4. Keep live broker order routing disabled. Do not close existing issues solely because this tracker exists.

Use [release gates](P2P_RELEASE_GATES.md) for acceptance procedures and the
[implementation report](P2P_IMPLEMENTATION_REPORT.md) for its dated verification
boundary. Retain failed checks as failed evidence. This documentation review
does not provision public hosts, execute independent-host acceptance or authorize
live broker routing.
