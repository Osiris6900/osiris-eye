# Network release progress

Base candidate: `ce16dbde1e7b2377c82e5f14622238e60c04ff07`.
Status recorded 2026-11-07; no percentage-based completion claim is used.

| Gate | Evidence/status |
|---|---|
| Main CI | [Passed](https://github.com/Osiris6900/osiris-eye/actions/runs/42423590055) |
| Cross-host transport delivery | [Passed](https://github.com/Osiris6900/osiris-eye/actions/runs/42423906183) |
| Three-peer mesh/reconnect | [Passed](https://github.com/Osiris6900/osiris-eye/actions/runs/42423906170) |
| Real two-host recipient E2EE | [Initial run on GitHub runners](https://github.com/Osiris6900/osiris-eye/actions/runs/42424456468); failed; harness remediation and candidate rerun required |
| Real three-host recipient isolation | [Initial run on GitHub runners](https://github.com/Osiris6900/osiris-eye/actions/runs/42424459471); failed; harness remediation and candidate rerun required |
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

Both target `154a65111d92cbd344ad2cc1b49a80ed8f53cd28`; results must be
recorded before closing gates.
