# Risk kernel and paper execution — initial parallel delivery contract

Tracks #179. This file starts an isolated workstream; it is not evidence of feature completion.

## Existing verification entry point

```bash
cargo test --manifest-path crates/risk-kernel/Cargo.toml
```

## Required implementation and acceptance

Idempotent paper intents, simulated fills, position and cash ledger, independent limits, stale-data rejection, kill switch and restart reconciliation.

## Evidence contract

- Record exact commit SHA, runner/host identity, UTC timestamp, test command, exit status and sanitized logs.
- Keep synthetic and paper-trading pathways distinct from real exchange execution.
- Do not promote without passing CI, fault-path regression tests and reviewer approval.
- Never commit API keys, private keys, account state or personal data.

## Delivery sequence

1. Inventory relevant existing code and tests; identify a smallest failing regression.
2. Implement the missing behavior on this branch with focused unit and integration tests.
3. Run the listed verification and record reproducible evidence.
4. Open review, resolve findings, and only then consider merge.
