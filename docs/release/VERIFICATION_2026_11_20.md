# Resumed PR #447 evidence verification — 2026-11-20

The original candidate is `84f307bbc7fafaceea88d5ee628a1151795c6243` on
`codex/release-acceptance-evidence-20261114`. GitHub PR CI used test merge
`252970302366f8e305361571bb03ed9bd5cc7ad3`; both revisions have source tree
`5a8c233233b64ee3976f6eb7a663a45958c3cc13`. The merge parents are main
`f38fa9a5757efed904e9b0967e3b6d3be5cc78f7` and the original candidate.
The workflow-dispatched capacity and portable-ledger recovery runs used the
candidate directly. The GitHub API and retained provenance agree on these identities.

The [machine-readable record](VERIFIED_ARTIFACTS_2026_11_20.json) records observed
workflow results, ten actual downloaded ZIP hashes, every member hash and GitHub
expiry. The inventory remains **3 evidenced / 14 Partial / 3 Remaining**; none of
the seventeen outstanding gates is accepted. Live trading remains unauthorized.

## Historical candidate checks and downloaded artifacts

All eight PR workflows completed successfully on 2026-11-14: CI, Quant API CI,
Dependency Security Audit, release inventory, immutable action pins, new platform
modules and the #179/#182 evidence contracts. Core CI includes successful Python,
Rust and web jobs, browser MFA/independent-review/HTTPS-worker checks, actual local
Rust receipt-to-paper process recovery and local relay E2EE integration. This
checkpoint reads those completed results; it does not claim they tested changes
being developed on 2026-11-20.

| Downloaded evidence | Observed verification | Boundary |
| --- | --- | --- |
| Dependency SBOM, artifact `14112968650` | Existing offline verifier reproduced all 613 components and committed input/provenance hashes against the exact test merge; candidate tree matches. | Signature, licence and maintenance-risk decisions remain unapproved. |
| Receipt-to-paper, artifact `14113867109` | Existing verifier checked all 840 source files, two traces and ten audit rows. | Local processes; binary reproduction, receipt signature/origin re-verification and independent-host truth are not established by this verifier. |
| Browser, relay E2EE, systemd and offline market-to-paper artifacts | Original ZIP bytes downloaded and hashed; recorded workflow steps and artifact reports passed. | These reports were inspected, not independently rerun or certified here. Relay evidence is single-host process integration. |
| Actual API service recovery, artifact `14113008616` | Report records authenticated API access, online backup, SIGKILL, a different restore database, credential rotation and stable-ID continuation. Local RTO 379.797 ms; one acknowledged fill was missing after restore and reapplied once. | Synthetic paper orders on one disposable host; no zero-loss, production or independent-host RTO/RPO claim. |
| Portable ledger recovery, run `43594333571` | Both local and second-job restore reports passed; downloaded artifacts `14113533105` and `14113682461` retained. | Separate GitHub jobs do not independently attest distinct physical hosts or running-service DR. |

Every archive digest matches GitHub's reported SHA-256 and size. Original ZIPs
remain in `/tmp/osiris-pr447-evidence` in this task workspace. The relay artifact
expires **2026-11-21T12:22:55Z**; the other nine artifacts expire on **2026-11-28**,
at the exact times recorded in JSON. This task-local storage is temporary and is
not independently approved, durable, production or WORM custody. Hashes do not
preserve bytes after GitHub expiry or workspace loss. No raw source payload,
credential or private host address is published in this record.

## Two distinct capacity failures

The paused-outage [run 43593970718](https://github.com/Osiris6900/osiris-eye/actions/runs/43593970718)
failed before its sustained measurement. Host A's job `132444437427` attempted to
download `capacity-b-endpoint-43593970718-1` before it was available. Host B and C
jobs completed successfully. No retained capacity-report archive is available for
this run; it supplies no measured sustained-delivery result.

The traffic-on [run 43593983448](https://github.com/Osiris6900/osiris-eye/actions/runs/43593983448)
completed 100 peers / 10,000 publications over 957.404375 seconds with one Tokio
worker requested. Actual worker count is not independently verified. Its policy
checker correctly failed loss and aggregate-RSS requirements:

| Measurement | Result |
| --- | --- |
| Delivered / lost / duplicate deliveries | 9,819 / 181 / 0 |
| Publications during outage / while recipient stopped | 223 / 55 |
| Outage publications delivered after restart | 43 |
| Sampled recovery | 27,069 ms |
| Host A / B / C peak aggregate RSS bytes | 550,522,880 / 559,902,720 / 522,551,296 |
| Unchanged RSS limit per host | 536,870,912 bytes (512 MiB) |
| Policy failures | `maximum_lost_messages`, `maximum_rss_bytes_per_host:host-a`, `maximum_rss_bytes_per_host:host-b` |

Downloaded ZIP `capacity-report-43593983448-1` has SHA-256
`3bc7fb591d239138396438691c62f51eb2254be1b6c18c6815101ceff69086a6`.
Its original `sustained-100.json` has SHA-256
`191c25bebb0aca919aa72bf21c84b65f4809fe3b886267aedbc1af86c5783b40`.
The resource metric sums sampled live-process RSS, including shared pages per
process, and includes the controller on host A. No threshold or metric is changed
to relabel this failed run. The topology remains a recipient-bootstrap star;
NAT/open-mesh capacity, host independence and T04/T19 acceptance are unverified.

## Resumed source verification and remaining acceptance

Source candidate `f29d961882ee3b6c564e509119155609d95e1adf`, tree
`e0a1c88ccddc3960f1205ff30a50b713d871286c`, fixes three confirmed races:
BLS ingestion now uses the exact verified in-memory observations, the real API
recovery exercise isolates a single worker from inherited Uvicorn settings, and
capacity handoffs retry until actual same-run/attempt artifact bytes validate.
All capacity builds use the committed Cargo lockfile. The current action
inventory is 19 workflows, 119 immutable action uses and zero mutable uses.

All **31 engineering checks passed** on this clean, unchanged source candidate.
The JUnit evidence contains **2,245 cases, zero skips and zero failures**, with
26 subtests also passing. Locked P2P Rust tests passed 247 cases, native Rust
tests passed three cases, frontend tests passed 119 cases, and all ten existing
Chromium/TLS browser flows passed. Actual local receipt-to-paper, paper failures,
portable recovery, dependency/provenance and source-integrity checks passed.
The complete engineering report has SHA-256
`1074c8a189952e63025c84d2fe98ef01f81aed7ea5857ed387714b4f532482ad`.

The exact-source authenticated service recovery measured **585.606 ms local
RTO**. One acknowledged post-backup fill was actually missing at restore and
was then reapplied exactly once. This is one-host synthetic paper evidence,
with production/independent-host acceptance still false.

Fresh official BLS capture and offline verification passed: three series,
66 observations, 64 numeric and two officially unavailable October 2025 values.
No unavailable value was imputed, and numeric-feed completeness remains false.
Fresh Coinbase capture retained 32 distinct quotes over 95.915 seconds and
reproduced the original archive offline. Separate fitted-ridge diagnostics
reproduced four purged folds. Fixed cost plus quoted-spread sensitivity returned
approximately +0.102916%, -1.091811% and -2.860142% at 0/10/25 bps, respectively,
with unit turnover 12. This short, post-capture diagnostic is neither a blinded
holdout nor representative profitability or execution-calibration evidence.
No operator feed policy was invented and no research-store ingestion ran.

The JSON record binds all report/capture digests to the tested source and runtime.
Private original bytes and complete local reports are also retained at
`/workspace/shared/osiris-eye-evidence-20261120` in this task workspace. This
directory remains task-local custody, with the same durability limits as above.
This documentation checkpoint adds no implementation changes after the tested
source commit. New paused/traffic-on independent-host measurements are still
required against the unchanged loss/resource policy; no new operational pass is
claimed and the historical failures remain failures.

Final-candidate dependency acceptance and the residual `paste` disposition,
original source-use rights, independent signed crypto/model/risk/compliance
decisions, representative blinded data, calibrated execution, sustained
independent-host operations, approved service DR custody/targets and authorized
paper-only release/rollback signoff remain required. T06, T09 and T20 remain
Remaining; all previously Partial rows remain Partial.
