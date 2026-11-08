# CI and reproducible staging — initial parallel delivery contract

Tracks #181. This file starts an isolated workstream; it is not evidence of feature completion.

## Existing verification entry point

```bash
python deploy/p2p/test_preflight.py
```

## Required implementation and acceptance

Resolve advisory reachability and dependency locks, pin GitHub actions to SHA, generate provenance/SBOM, stage smoke test, secrets checks and disaster recovery exercise.

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
