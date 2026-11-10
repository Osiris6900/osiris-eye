# T06 independent cryptography review — proposed commissioning scope

Status: **DRAFT FOR EXTERNAL REVIEWER AGREEMENT**. Tracker: #323; related #177, #179, #182. This document is a proposed statement of work, **not** a commissioned audit, signed report, security approval, or release-gate acceptance. No reviewer or organization has been appointed.

## Review baseline and independence

- Record the immutable 40-character commit SHA and exact reviewed dependency lockfiles, binary build provenance and configuration before beginning. Any later code change requires an explicit delta review.
- Reviewer must be independent of the change authors, identify their organization and relevant cryptography/security qualifications, and disclose conflicts of interest. The operator must agree to scope, reviewer, access arrangements and report delivery.
- Use only authorized synthetic test identities, keyless data, and PAPER-ONLY execution. No live broker/exchange orders, real credentials, public operator identity or secret disclosure.

## Minimum technical scope

1. **Key lifecycle:** key generation and entropy assumptions, exchange/authentication, peer identity binding, persistence, rotation, revocation, restart and zeroization; identify who can impersonate a peer.
2. **Recipient-only E2EE:** protocol primitives, nonce uniqueness, associated data, ciphertext integrity, metadata leakage and relay trust; demonstrate wrong-recipient and altered-ciphertext rejection.
3. **Replay and recovery:** message identifiers, delivery receipts, duplicate/reordered messages, replay across process restarts, stale or revoked peers, offline queues, clock skew and downgrade handling.
4. **Network trust:** bootstrap peers, relays, NAT traversal, peer allowlists, malicious or partitioned hosts, denial-of-service/resource exhaustion, network observability and isolation assumptions.
5. **Operational security:** ghost/keyless boundary, secret and identity exposure through logs, artifacts, dashboards, CI jobs and dependency supply chain; incident containment and revocation.
6. **Paper-risk boundary:** verify no reachable path can submit live exchange/broker orders; audit order-intent controls, idempotency, failure behavior and fail-closed defaults.
7. **Test provenance:** reproduce representative positive and negative tests at the pinned commit, map each claim to commands, exit codes, sanitized logs and retained immutable artifacts. Identify untested threat scenarios and limits of single-host simulations.

## Deliverables and decision contract

- Signed report or independently verifiable signed report reference, report date, reviewer name and organization, precise SHA, methodology, test environment, threat model, scope exclusions, findings with severity and reproduction evidence.
- Remediation matrix linking each finding to an exact fix commit, regression test and independent retest; explicit unresolved critical/high count.
- Explicit reviewer decision (approved, conditional, or rejected) and any residual risks. An approved decision alone does not authorize live trading.
- Record artifact locations and digests in the existing evidence inventory only **after** independent verification. Keep T06/T20 blocked until the existing release-evidence validators and acceptance criteria are genuinely met.

## Commissioning checklist

Operator action required: select and approve an independent reviewer, agree on the final scope and disclosure/handling rules, provide authorized access, and obtain a signed report. Do not treat this draft as operator approval. Do not modify release gates to accommodate missing evidence.
