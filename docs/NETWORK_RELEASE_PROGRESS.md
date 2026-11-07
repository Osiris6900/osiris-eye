# Network release progress

Base candidate: `ce16dbde1e7b2377c82e5f14622238e60c04ff07`.
Status recorded 2026-11-07; no percentage-based completion claim is used.

| Gate | Evidence/status |
|---|---|
| Main CI | [Passed](https://github.com/Osiris6900/osiris-eye/actions/runs/42423590055) |
| Cross-host transport delivery | [Passed](https://github.com/Osiris6900/osiris-eye/actions/runs/42423906183) |
| Three-peer mesh/reconnect | [Passed](https://github.com/Osiris6900/osiris-eye/actions/runs/42423906170) |
| Real two-host recipient E2EE | [Started on GitHub runners](https://github.com/Osiris6900/osiris-eye/actions/runs/42424456468); result pending |
| Real three-host recipient isolation | [Started on GitHub runners](https://github.com/Osiris6900/osiris-eye/actions/runs/42424459471); result pending |
| Local modified API | 865 tests passed with Rust integration tests enabled |
| Local relay E2EE | Passed; dirty checkout, single-host regression evidence only |
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
