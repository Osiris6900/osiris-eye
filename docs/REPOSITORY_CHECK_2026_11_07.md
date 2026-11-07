# Repository validation — 2026-11-07

Reviewed base: `ce16dbde1e7b2377c82e5f14622238e60c04ff07`, with the local
authentication fix and evidence documentation changes described below.
This is an initial code/security review and reproducible validation record,
not an independent security audit or production certification.

## Fixed finding

The bearer-token middleware passed Python strings to `hmac.compare_digest`.
That function rejects non-ASCII strings, so a non-ASCII Authorization header
caused HTTP 500 instead of HTTP 401 when authentication was enabled. A
non-ASCII configured token also caused the comparison to fail with a server
error. Two regression tests reproduced these failures before the change.

The middleware now compares UTF-8 bytes using the same constant-time primitive.
Both rejection cases return 401. Existing valid-token and production
missing-token tests remain passing.

## Local validation

Environment: Python 3.12.14, Node.js 24.22.1, Rust 1.99.1.
Python CI uses 3.11; local Python results do not replace that CI job.

| Check | Result |
|---|---|
| Full Python API suite | Final rerun: 865 passed, zero skipped |
| Previously skipped Rust receipt bridge test, rerun after setup | 1 passed |
| Previously skipped node retry process test, rerun after build | 1 passed |
| Frontend behavior tests | 11 passed |
| TypeScript check and Vite production build | Passed |
| npm audit | 0 reported vulnerabilities; npm ecosystem only |
| Rust risk kernel | 2 passed |
| Locked P2P Rust suite, all targets | 165 passed |
| Workflow YAML and embedded shell validation | 8 workflow files, 79 shell blocks passed |
| Workflow checker regression tests | 11 passed |
| Workflow coordination tests | 10 passed |
| SSH harness tests | 10 passed |
| Deployment preflight tests | 7 passed |
| `test_check_p2p*.py` evidence-checker suites | 28 passed |
| Whitespace/error check (`git diff --check`) | Passed |

Existing compiler warnings and a Starlette/httpx deprecation warning remain.
All 59 remote CI action references are now pinned to full commit SHAs; the
mutable-reference baseline is empty and the checker passes. A local core relay
E2EE run also passed, including exact recipient digest and relay-only paths.
Its evidence is `/tmp/osiris-core-relay-e2ee-current.json` (dirty working tree,
single-host process integration).

## Release evidence and outstanding work

Release/security documents now link the successful main CI run at `ce16dbde`
and distinguish the successful Tailscale cross-host delivery run at `974de9ce`
from recipient E2EE acceptance. Those remote runs predate this local fix;
the modified candidate still needs its own remote CI result after publication.

Independent two-host and three-host recipient E2EE, final-candidate multi-host
load/outage soak, public operations, feed-rights evidence and an independent
security audit remain release gates. They require operator infrastructure,
external evidence or reviewers and were not established by these local tests.
See [release gates](P2P_RELEASE_GATES.md) for acceptance criteria.

No permanent deployment or live trading was performed. Publication and remote
acceptance status are tracked in [network progress](NETWORK_RELEASE_PROGRESS.md).
