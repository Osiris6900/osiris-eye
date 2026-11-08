# Cross-module end-to-end acceptance — initial parallel delivery contract

Tracks #182. This file starts an isolated workstream; it is not evidence of feature completion.

## Existing verification entry point

```bash
python scripts/test_p2p_workflow_coordination.py
```

## Required implementation and acceptance

P2P signed observation -> validated API ledger -> research signal -> risk-gated paper intent -> simulated fill -> reconciled dashboard, including fault injection.

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
