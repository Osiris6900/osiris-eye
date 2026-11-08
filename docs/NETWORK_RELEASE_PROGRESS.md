# Network release progress

Base candidate: `ce16dbde1e7b2377c82e5f14622238e60c04ff07`.
Status recorded 2026-11-07; no percentage-based completion claim is used.

| Gate | Evidence/status |
|---|---|
| Main CI | [Passed](https://github.com/Osiris6900/osiris-eye/actions/runs/42423590055) |
| Cross-host transport delivery | [Passed](https://github.com/Osiris6900/osiris-eye/actions/runs/42423906183) |
| Three-peer mesh/reconnect | [Passed](https://github.com/Osiris6900/osiris-eye/actions/runs/42423906170) |
| Real two-host recipient E2EE | [Passed at `154a651`](https://github.com/Osiris6900/osiris-eye/actions/runs/42424830531) |
| Real three-host recipient isolation | [Passed at `154a651`](https://github.com/Osiris6900/osiris-eye/actions/runs/42424833409); B exact digest, C explicit discard |
| Local modified API | 865 tests passed with Rust integration tests enabled |
| Local relay E2EE | Passed; dirty checkout, single-host regression evidence only |
| Python dependency audit | 0 known vulnerabilities after cryptography 50.0.2, pytest 9.2.0 and pip 26.3; local environment only |
| Immutable CI action references | 59 references pinned locally; empty mutable baseline; workflow checker passed |
| Multi-host load/outage soak | Pending: 100 peers, 10,000 messages, at least 900 seconds under the existing policy |
| Public operations | Pending: permanent operator-controlled hosts, endpoints, monitoring and recovery evidence |
| Feed rights/quality | Pending: actual provider authorization and independent quality evidence |
| Independent security audit | Pending: external reviewer and retained findings/retest evidence |

The remote E2EE runs use fresh temporary GitHub runners and the base candidate.
They do not validate later local edits or demonstrate permanent public uptime.
Record their exact outcome and assertions before closing either gate.

The proposed changes fix bearer authentication errors for malformed non-ASCII
headers and pin CI action code. They do not add live execution. See the
[validation record](REPOSITORY_CHECK_2026_11_07.md) and
[release criteria](P2P_RELEASE_GATES.md).

The initial two-host run failed because the shared SSH launcher exported an empty
`OSIRIS_TRUSTED_PEERS`. The node correctly rejected it. The launcher now unsets
throwaway-test peer-policy variables, including inherited file settings, without
weakening node validation. A shell-level regression verifies this isolation.
The initial three-host run also failed; a shared-launcher fix is included for
its rerun, and the gate remains open until the candidate succeeds.

Python bounds now require cryptography >=50,<51 and pytest >=9.0.3,<10; CI
upgrades pip >=26.2 before installation. Full local tests passed after the
upgrade (865); `pip check` passed. The audit inventory is
[retained here](PYTHON_DEPENDENCY_AUDIT.json). Advisory matches are point-in-time
and do not establish exploitability or independent security assurance.

Remote Python CI at `154a651` exposed a concurrent retry-result reporting race.
Only the winning worker sent, but a losing worker could return the same
`published` snapshot as a new outcome. A deterministic interleaving reproduces
the failure; losing claims now emit no action outcome. The full local suite
passed again (865). Node/E2EE source is unchanged by this Python-only fix.

Fixed-launcher remote acceptance reruns:
- [Two hosts](https://github.com/Osiris6900/osiris-eye/actions/runs/42424830531)
- [Three hosts](https://github.com/Osiris6900/osiris-eye/actions/runs/42424833409)

Both target `154a65111d92cbd344ad2cc1b49a80ed8f53cd28`; both concluded success. The sanitized log/status summary is
[P2P_REMOTE_ACCEPTANCE_2026_11_07.json](P2P_REMOTE_ACCEPTANCE_2026_11_07.json).
Final release evidence still needs binary hashes and exact final-candidate runs.

## Measured soak work

Candidate `976858e` added the opt-in collector and workflow. Local four-peer
smoke delivered 30/30 messages with a sampled 6.8-second bootstrap recovery;
this is single-machine regression evidence only. A 20-peer debug-build local
scale smoke subsequently failed to observe delivery recovery within 60 seconds.
That failure is retained as a scalability investigation, not acceptance.

The [initial real soak attempt](https://github.com/Osiris6900/osiris-eye/actions/runs/42426248895)
failed before traffic: the coordination helper rejected the workflow's longer
receiver timeout. The helper now permits a bounded 2700-second wait, tested for
the 2400-second soak setting and invalid/infinite limits. Sanitized per-host
publish/decryption diagnostic counters were added to investigate scale failures.
The 100-peer gate remains open until an actual measurement passes the unchanged
policy; no successful load acceptance is claimed here.

## 2026-11-08 scale remediation

The [100-peer attempt at `8ea71eb`](https://github.com/Osiris6900/osiris-eye/actions/runs/42426670853)
started all peers but timed out before warmup/workload. Aggregate RSS reached
3.5–3.9 GiB per runner and sampled CPU reached 93–96 percent. This is a failed
readiness/resource result, not a completed 15-minute soak.

The candidate now batches key-binding announcements only for relevant topic
subscriptions, limits announcement retries and avoids writes for identical
verified bindings. Signature, epoch, rollback and corrupt-index validation are
retained. The soak enforces its declared star instead of allowing discovery to
expand it into a full mesh. Four runners host 25 peers each without relaxing
limits. Open-mesh capacity is explicitly untested by this experiment.

The collector also retains late probes, drains existing workload before the
induced outage, invalidates stale subscription state, and requires encrypted
delivery from every publisher after restart before scheduled load resumes.
These conditions measure drained restart recovery, not in-flight durability.

The [local release-build regression](P2P_SCALE_REGRESSION_2026_11_08.json)
delivered 200/200 messages over 20 peers, with no lost/pending outcomes. p95
was 36 ms and p99 57 ms. This source-dirty, single-machine regression includes
source-file hashes; it cannot close the real multi-host gate. A new candidate
soak and its retained policy result remain required.
