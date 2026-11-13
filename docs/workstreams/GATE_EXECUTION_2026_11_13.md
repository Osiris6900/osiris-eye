# Remaining-gate implementation and verification — 13 November 2026

Baseline: main `35fd514d06f7d897b1aa68be792f3b803abd69dc`.
The existing #431 fixes are integrated on this baseline. The release inventory
remains **3 evidenced / 14 partial / 3 remaining**; live execution is disabled.

## Implementation

- Retain the calendar local-date/overnight, known-closed-day, DST validation and
  in-memory persistence fixes from #431.
- Retain SQLite timestamp/sequence bounds and eligible-source filtering from
  #431, including their regressions.
- Extend identity clock fencing to *refused* enrollment and MFA operations.
  Previously, a failed MFA verification rolled its clock observation back with
  the business transaction. An expired enrollment could then be revived after a
  clock rollback. A rejected re-enrollment similarly failed to fence an older
  session. A savepoint now rolls back business writes while committing the
  observed clock, including across process restarts. Existing seeds and MFA
  state are unchanged after a refused operation.
- Two additional regression tests reproduce these refusal/rollback cases.
  A before/after exercise observed `expired_enrollment_revived` on the previous
  implementation and `identity_clock_rollback` on the corrected implementation.

## Verification boundaries

Use the committed Python deployment lock plus development dependencies, Node 24,
the npm lock, Rust and Chromium. API/browser/P2P exercises need local IPC and
test sockets. The source-bound engineering profile runs all 29 checks and binds
the exact committed checkout and artifact digests:

```bash
python scripts/verify_all.py --profile engineering --execute \
  --report /tmp/osiris-gate-engineering.json
python scripts/check_release_gate_status.py --strict
```

The strict check must continue to refuse the seventeen unaccepted release gates.
Do not change an acceptance flag merely because the engineering profile passes.
Reports and sensitive disposable identities belong outside the source checkout.

## Requirements that still need genuine external evidence

| Gates | Required evidence/work |
| --- | --- |
| T01 | Final-candidate advisory/SBOM/provenance acceptance and independently reviewed disposition of the `paste` maintenance advisory. |
| T04 | Sustained independent-host NAT/relay/open-mesh load with traffic during outages, measured loss/latency and retained recovery evidence. |
| T06 | Actual independent crypto/operations reviewer, authenticated signed report, remediation/retest and decision. |
| T07–T10 | Original source rights, authorized provider configuration, genuine multi-asset/stream/corporate-action/macro captures and provenance review. |
| T11–T12 | Frozen genuine data/model/features, blinded OOS/leakage review and calibrated cost/turnover/impact/borrow sensitivity. |
| T13 | Independently owned/approved budgets, limits, drawdown/concentration and exception governance. |
| T14 | Authorized paper-broker operational reconciliation. This remains deferred; no live broker routing is authorized. |
| T15 | Venue-calibrated queue/liquidity/impact/margin/borrow models and genuine multi-asset execution comparison. |
| T16–T17 | Independent service-outage acceptance and sustained authenticated monitoring/alert delivery with genuine configured sources. |
| T18–T19 | Approved independent-host service backup/restore/failover, measured RTO/RPO, custody and final-candidate operator acceptance. |
| T20 | Independent security/model-risk/licensing/compliance decisions and authorized paper-only release signoff. |

No independent reports, original licences, vendor/broker credentials, or approved
external staging/host targets were configured for this execution. Local fixtures,
containers on one machine and self-signed reports cannot supply those results.
The broader product/R&D packages #413–#416 also retain their stated boundaries.
