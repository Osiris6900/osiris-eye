# Pending PR integration — 2026-11-27

This candidate combines the pending implementation branches against main
`0fde70b714d247b52ef1bc01a629256dd1dfa40b`. It prepares one reviewable integration
candidate; it does not accept release gates or authorize live execution.

## Included branches

| PRs | Integrated behavior |
| --- | --- |
| #447–#450 | Official macro capture, ridge diagnostics, authenticated service recovery, durable P2P publisher recovery and capacity evidence |
| #451–#452 | Terminal broker day status and canonical day-order expiry |
| #454 | Server-ledger pretrade exposure and independent risk decisions |
| #455 | Independent paper-broker snapshot reconciliation |
| #456–#457 | Signed audit evidence and versioned control-plane key management |
| #458–#459 | Market-data admission and trusted receive-clock provenance |
| #461 | Durable paper-event delivery, consumer acknowledgement and replay |
| #469 | Rechecked fill clock health, order release under a broker halt and ordered live-event recovery |
| #467, #470 | Sealed shadow runs/comparisons, exact finite arithmetic, bounded allocation and v1 record compatibility |

The current main instrument catalog, executable tick/lot validation, depth
midpoint handling and newer dependency advisory/provenance records are retained.
The stack's historical measurements retain their original candidate identities.

## Cross-feature corrections

- Expiry, cancellation and all three broker-reconciliation HTTP entry points
  reject caller receive clocks. Tests inject the trusted server clock rather
  than sending a decision clock in a request body.
- Broker authorization and broker reconciliation use distinct clock helpers;
  the combined module cannot silently override one with an incompatible signature.
- Order submission retains venue/time-in-force fields together with clock,
  instrument, data-quality and canonical-ledger risk checks.
- Every canonical journal transaction uses bus-aware commit/rollback. Expiry,
  cancellation refusal, broker reconciliation, risk decisions and rejected market
  observations reach the appropriate consumer stream after commit.
- Journal-backed pull, replay and live notifications verify their retained
  projection against the canonical hash chain in the same read transaction.
  A modified delivery payload or broken journal is refused. Standalone published
  events remain separate from journal authority.
- HTTP fills retain the server clock's identity and measurements; assigning a
  server timestamp does not label it as an injected caller clock.
- Newer-main identity/MFA fixtures use the strict versioned-key configuration.
- Shadow API MFA fixtures also declare versioned random test key material,
  matching the integrated identity store rather than relying on an unversioned
  placeholder key.
- The consolidated reports route verifies the listed shadow records before
  returning them, preserving the fail-closed behavior of dedicated run reads.

## Review follow-up sources

The combined candidate includes paper review fixes at
`37f6855bcd73de3bb8a20556beb2696386dbe1e2` and shadow review fixes at
`6cb2a7f24a04445123fa84dbdff34818ada11e5e`. Both standalone candidates passed
their applicable GitHub checks before integration. The shadow changes retain
previous-tick execution and zero broker submissions; the new numerical policies
and historical-record rules are documented in
[`SHADOW_NUMERICS.md`](../SHADOW_NUMERICS.md).

Combined validation results and the exact tested commit are recorded in the
integration PR. Scoped backend/frontend/browser checks are not a claim that
the full 33-check engineering profile or independent release acceptance ran.

## Verification

Run with the locked Python dependencies, Node 24, Rust stable, Chromium and the
project's development test dependencies installed:

```sh
python scripts/verify_all.py --profile engineering --execute --report /tmp/osiris-integration.json
python scripts/scan_committed_secrets.py
cargo audit --file services/p2p-node/Cargo.lock
cargo audit --file crates/native-feed/Cargo.lock
cargo audit --file crates/risk-kernel/Cargo.lock
```

The local acceptance fixtures use an explicit synthetic healthy host-clock
declaration through `OSIRIS_CLOCK_HEALTH_JSON`; negative tests install degraded
clock states. This declares a test input, not independently measured host health.
Production clock health remains fail-closed when unavailable. Reports bind the
tested commit, source snapshot, outcomes and artifact digests. Actual results and
the report's tested commit are recorded in the integration PR.

The gate inventory remains **3 evidenced / 14 partial / 3 remaining**.
Independent source rights, security/model/risk review, representative blinded
data, execution calibration and final-candidate independent-host operational
acceptance remain outstanding. The audit PEM and versioned private-key-ring
interfaces remain explicitly separate as documented in
[`SECRETS_AND_KEY_MANAGEMENT.md`](../SECRETS_AND_KEY_MANAGEMENT.md).
