# Operator evidence runbook — T18 recovery and T04/T19 P2P capacity

Status: **PLANNED / EVIDENCE NOT YET ACCEPTED**. This runbook does not dispatch workflows or certify gates.

## Preflight
- Record immutable main SHA, approved paper-only environment, expected runner/host classes, retention duration and operator authorization.
- Confirm no live broker connectivity, no real secrets in logs, and keyless synthetic identities.
- Check workflow definitions and acceptance scripts against current main; never reuse stale artifacts from another SHA.

## T18 — disaster recovery
- The local paper-ledger CI backup/restore exercises a single-runner synthetic fixture.
  A follow-on job now downloads the hashed synthetic backup artifact into a
  separate GitHub-hosted runner job, verifies source files and digests, restores
  through the real paper execution module, reconciles and continues one paper
  order. Preserve both job artifacts and hashes. **Do not infer physically distinct
  hosts, a running service failover, operator data, RTO/RPO, or production DR**.
  T18 stays Partial.
- Design an authorized independent-host backup transfer and restore drill with failure injection, health checks, state reconciliation and isolation from real operator data.
- Measure RTO from service failure to recovered healthy response; measure RPO from last durable event to recovered durable state. Capture clock basis, units, sample size, limits and negative outcomes.
- Retain sanitized source/restore digests, exact commit, workflow run and attempt, host class, UTC timestamps, logs and artifact expiry.
- T18 remains Partial until real evidence meets the existing release gate. Do not label a validator-only dispatch as recovery.

## T04/T19 — P2P capacity
- Operator alone may dispatch capacity or recovery workflows. Never automate or silently trigger them.
- For each required 10/50/100-peer configuration, record distinct hosts, peer count, message count, duration, loss, error rate, latency percentiles, CPU/memory, reconnect/replay results and failure modes.
- Verify that evidence was produced on the intended SHA, each required job succeeded and retained artifacts have calculated SHA-256 digests and retention metadata.
- Preserve unsuccessful runs and disclose limits; a 100-peer result alone does not establish all configurations if acceptance requires each.

## Evidence acceptance
- Update JSON and Markdown gate records together only after verifying real artifacts and the required acceptance criteria. Keep PAPER-ONLY and ghost/keyless boundaries intact.
- Independent crypto review is tracked separately in #323; it cannot be replaced by CI green.
