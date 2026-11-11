# T06 — Independent cryptography/security review intake

**Status: Not commissioned, no signed independent report, T06 Remaining.**

This is a checklist and a *schema-only* intake verifier, not a security certification. No auditor, review outcome, independence or external signature is claimed by this repository change.

## Commissioning package

An operator must appoint an independent reviewer with disclosed conflicts, organization, named lead and agreed scope. Supply a pinned 40-character candidate commit, threat model, code/build reproducibility instructions, test artifacts, operator topology, key lifecycle and incident runbooks. Review E2EE handshake and authentication, identity keys and rotation/revocation, replay protection, encrypted transport, bootstrap/relay/observer boundaries, NAT assumptions, CI secrets, logs, and failure behavior. Include explicit non-goals and operator permission for any active testing.

## Deliverables and gate conditions

1. Independently signed report with reviewer identity, organization, date, scope, methodology, exact reviewed commit and verified signature.
2. SHA-256 for each immutable report, reviewer attestation and finding register, with stable HTTPS links under operator control.
3. Severity-ranked findings, exploitability, remediation commit, retest proof and disposition of critical/high issues.
4. Independent verification of reviewer identity, signature, hashes, final candidate code, remediation and operator signoff.
5. No T06 acceptance until the release owner explicitly records an approved decision and updates the release gate inventory with verifiable evidence.

`python scripts/check_external_crypto_review_intake.py path/to/intake.json` validates the required fields, declared scope and artifact URL/hash shapes. **It does not fetch URLs, verify signatures, verify reviewer independence or accept T06.** Do not commit secret keys, credentials or private audit findings. Keep live broker execution disabled and the release gate `Remaining` until external review is actually completed.
