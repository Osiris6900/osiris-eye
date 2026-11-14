# T06 independent security reviewer intake (operator checklist)

Status: **NOT COMMISSIONED / NOT APPROVED**. Issue #323. This is a handoff template, not audit evidence.

## Before engagement (operator-owned)
- Select an independent reviewer or organization, confirm qualifications, independence, conflicts and scope in `docs/security-review-commission-scope.md`.
- Agree to a private access method, disclosure terms, report ownership and secure contact channel. Do not publish identities, access tokens, private keys, credentials or operator location in CI artifacts.
- Freeze the exact reviewed Git commit SHA, dependency lockfiles, test commands and configurations; require a written delta assessment if code changes.
- Keep all execution PAPER-ONLY, ghost/keyless. Never provide live exchange permissions.

## Required reviewer deliverables
- Reviewer organization and accountable signatory in a restricted-access record, scope, date, methodology, threat model and exclusions.
- Findings by severity with reproducible sanitized evidence and fix commits; independent retest evidence for critical/high findings.
- Signed report or verifiable signed report reference with immutable SHA-256 and storage/retention location.
- Explicit decision and unresolved critical/high count. A decision is not live-trading authorization.

## Acceptance gate
T06 stays blocked until an authorized operator verifies independent reviewer provenance, signed report, test scope and findings against the exact SHA. Do not fabricate a reviewer or treat automated CI as independent audit.

The intake checker requires both `approved` and `approved_with_remediation`
declarations to attest independence and conflict disclosure and reference a signed
report. Unconditional `approved` intake cannot contain open critical/high findings.
Conditional intake may retain open remediation work; pending or rejected intake
may honestly declare that independence has not been attested. These checks only
enforce internal consistency: they do not verify a signature or accept T06/T20.
