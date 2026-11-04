# P2P hardening implementation report

## Scope

This delivery strengthens the encrypted market-data observation network for
research and paper trading. It adds core-node relay support, stricter
cryptographic and timestamp validation, durable-state safeguards, safer
enrollment/replay handling, repaired cross-host workflows, and an operator
deployment profile. It does not connect observations automatically to the paper
simulator or enable live execution.

## Local verification

The following local checks passed on 4 November 2026 before publishing this
branch. The baseline is `48755ef3`. GitHub CI and cross-host workflow results
should be read against the exact candidate revision and attempt.

| Check | Observed result | Evidence boundary |
|---|---|---|
| Rust P2P all-target suites | 122 tests passed across all targets | Includes final payload-admission and relay-address regressions |
| Python API | 264 tests passed | Includes replay, concurrent enrollment and capacity API regressions |
| Core relay E2EE process acceptance | Passed; both endpoint connections relayed, signed bindings durable, exact recipient payload digest received | One machine, three independent processes; not public-network or NAT proof |
| Three-process recipient E2EE | Recipient delivered the exact digest; excluded observer reported recipient-gated discard | Does not claim that the observer attempted cryptographic decryption |
| Workflow artifact coordination | 10 tests passed | Includes deadline and rerun coordination checks |
| SSH harness behavior | 10 tests passed | Includes out-of-order events, bounded output and process cleanup |
| Deployment preflight | 7 tests passed | Profile validation; no host service was installed or started |
| Freshness process acceptance | Passed | Authenticated stale/future rejection followed by exact fresh recipient delivery |
| Workflow linter | 5 tests passed; 6 YAML files and 66 embedded shell blocks validated | Syntax validation does not replace a real Actions run |

The relay evidence record explicitly identifies its scope as
`single-host-process-integration`, includes binary hashes and public peer IDs,
and marks the source dirty. Private identities and runtime databases were absent
from the files selected by the repository's ignore rules during review. A
targeted secret-pattern inspection found only a synthetic private-key marker in
a redaction test; this is not a comprehensive secret-history audit.

## Promotion gates

Before public promotion, retain passing results against the final reviewed
commit for all local and CI checks, including the new freshness acceptance.
Then complete these remaining gates:

- Two- and three-independent-host E2EE acceptance, with attempt-bound workflow
  artifacts and sanitized logs.
- Relayed delivery across the intended networks with direct dialing blocked,
  including reservation expiry, reconnect and quota denial behavior.
- Operational identity/key rotation and revocation recovery after restarts and
  partitions; private state remains single-writer per path.
- Declared load and outage benchmarks, sustained resource bounds, monitored
  public endpoints and recovery procedures.
- Authorized feed provenance, redistribution rights and validated downstream
  paper-trading/risk integration.
- Independent cryptographic and security review, with findings remediated and
  retested.

The v1 construction exposes routing metadata and has no ratchet guarantee
against later compromise of a recipient's static X25519 key. Explicit relay
configuration does not implement automatic relay selection or hole punching.
No public-fleet uptime, live peer count, production certification or comparative
“world best” result is established by this implementation report.

See [release gates](P2P_RELEASE_GATES.md),
[security model](P2P_SECURITY_MODEL.md) and
[hardened receiver deployment](P2P_HARDENED_DEPLOYMENT.md).

## Parallel workstreams

Twenty-four specialist agents worked in batches within the six-worker concurrency
limit. Reviews below are internal engineering reviews, not independent audits.

| Workstream | Delivered work |
|---|---|
| 01 | Cross-host workflow YAML, readiness, completion and host-key pinning fixes |
| 02 | Core-node Circuit Relay v2 integration and connection limits |
| 03 | Cryptographic input validation and adversarial regressions |
| 04 | Durable peer-key and revocation integrity safeguards |
| 05 | Python replay, enrollment and protocol validation hardening |
| 06 | Core relay E2EE process acceptance and JSON evidence |
| 07 | SSH harness event ordering, bounded output and cleanup |
| 08 | UTC timestamp parsing and freshness policy |
| 09 | Bounded, fail-closed replay-journal recovery and append checks |
| 10 | Hardened receiver service, preflight and operator guide |
| 11 | Fail-closed peer configuration and bounds |
| 12 | Security and rollout documentation corrected against implementation |
| 13 | Enrollment-capacity API responses and endpoint tests |
| 14 | Real-process freshness and malformed-configuration acceptance |
| 15 | Workflow trust-boundary and rerun-race review |
| 16 | Review fixes: trusted CI origin, attempt isolation and polling deadlines |
| 17 | Rust security integration review |
| 18 | Release criteria and current-scope documentation |
| 19 | Review fixes: payload validation before replay admission, configuration and feed resilience |
| 20 | Required CI checks, workflow linter and evidence retention |
| 21 | Python security review and persistent-registry configuration regression |
| 22 | Deployment review, required address family and preflight consistency |
| 23 | Dependency inventory and reproducibility review |
| 24 | Internal acceptance/evidence review and this implementation report |
