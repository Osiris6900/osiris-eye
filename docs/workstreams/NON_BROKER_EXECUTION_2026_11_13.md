# Non-broker release execution — 2026-11-13

Scope: keep the existing Ghost/keyless default and optional paper-broker setting.
Actual external brokerage connection, account access and broker acceptance are
deferred. Internal paper OMS, risk, reconciliation and accounting remain in the
local checks; deferring a broker does not waive those controls.

## Engineering changes

The advisory scanner previously queried only the P2P Cargo lock. The native-feed
lock adds `memmap2`, which was present in the combined SBOM but absent from the
advisory queries. The scanner now requires all three Rust roots, the npm lock,
Python manifest and hashed Python deployment lock. It deduplicates shared exact
packages, rejects conflicting checksums and binds all six input hashes before
and after scanning. The CLI compares those inputs with their recorded source
commit. Missing history, missing locks, changed inputs or incomplete coverage
fail verification.

OSV continuation pages are now consumed with per-package deduplication and a
bounded pagination budget. A malformed, failed or repeated continuation fails
the scan instead of silently omitting later advisories. Offline `--check` never
constructs a network client and verifies the retained report against the same
complete input inventory. Dependency-security CI runs the scanner regressions
and this offline check, with full Git history for source-commit verification.

The actual retained scan queried **608 exact packages** at
`2026-11-13T11:15:07Z`, from dependency inputs committed at
`2b685c2bc8689422724f1d9eb52ceef660ebb8a7`: 452 crates, 132 npm packages and
24 Python distributions. It returned **0 vulnerability groups and 1 existing
maintenance-only group** (`paste` / RUSTSEC-2024-0436). This is point-in-time
advisory evidence, not an independent security or licence approval.

The engineering profile now has **29 checks**. T01 additionally requires the
retained advisory check, hashed Python-lock check and licence/provenance policy.
Python subprocesses use the verifier's own interpreter. The backend suite emits
a retained, hashed JUnit XML report; missing/malformed reports, zero cases,
skips (including expected failures), or recorded errors cannot pass its check.
Regression tests exercise real successful, skipped and expected-failure pytest
subprocesses rather than relying on console-summary text.

## Reproduce

Run with the installed project Python environment, Node >=24, Rust, Chromium,
locked dependencies and local test sockets available:

```bash
python scripts/test_verify_all.py
python scripts/test_scan_dependency_advisories.py
python scripts/scan_dependency_advisories.py \
  --check docs/P2P_DEPENDENCY_ADVISORIES.json --fail-on-vulnerability
python scripts/verify_all.py --profile engineering --execute \
  --report /tmp/osiris-non-broker-engineering.json
```

The execution report records actual outcomes, source snapshots, per-gate check
coverage, artifact hashes and the JUnit evidence. The PR records its exact tested
commit and observed results. None of these commands contacts a real broker or
dispatches an external-host workflow. Provider/broker test inputs are fixtures.

## Requirements that remain open

| Gates | Required completion evidence or work |
| --- | --- |
| T01 | Acceptance of final-candidate audit/SBOM/provenance and reviewed disposition of the unmaintained dependency. |
| T04 | Approved independent hosts and sustained traffic-on NAT/relay/open-mesh outage/capacity acceptance. |
| T06 | Actual independent cryptography/operations reviewer, authenticated signed findings and retest. |
| T07–T10 | Original source-owner rights, configured authorized sources, genuine multi-asset/stream/corporate-action/macro captures and provenance acceptance. |
| T11–T12 | Frozen genuine candidate data/model/features, blinded cost-adjusted holdout and independent leakage/model review. |
| T13 | Independently owned and approved budgets, limits, exceptions and risk-governance decision. |
| T14 | External broker-paper acceptance explicitly deferred; internal OMS remains tested. |
| T15 | Venue-calibrated liquidity/queue/impact and margin/borrow models, with genuine multi-asset execution comparison. |
| T16–T17 | Independent service-outage acceptance and sustained genuine authenticated monitoring/alerts. |
| T18–T19 | Approved independent-host recovery/failover, measured RTO/RPO, custody and operational acceptance on the final candidate. |
| T20 | Independent security/model-risk/licensing decisions and authorized paper-only operator signoff. |

No external reviewers, original licences, genuine candidate dataset or approved
independent-host targets are configured in this task's environment. Local
fixtures and signed self-attestations cannot supply those independent results.
The wider product/R&D backlog is separate from this release-gate verification.

The JSON and Markdown release inventories retain **3 evidenced / 14 partial /
3 remaining**: all 17 previously unaccepted gates remain open. Broker deferral
narrows this task; it does not turn T14 into accepted evidence. Live execution
remains disabled.
