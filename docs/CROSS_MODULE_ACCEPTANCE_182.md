# #182 — Cross-module acceptance matrix

This is an **evidence-chain validator**, not yet an executable integration
harness for the production Rust/Python/paper execution modules. Final
acceptance requires actual test runs, not mocked receipt records.

| Stage | Source | Required evidence |
| --- | --- | --- |
| rust_p2p_receipt | Real Rust P2P node, encrypted cross-host delivery | Receipt ID, exact commit, retained artifact SHA-256, CI run |
| python_ledger_import | Python ledger importer consumes P2P receipt | Matching input receipt ID, new output receipt ID, retained artifact |
| paper_execution | Paper OMS consumes imported ledger receipt | Matching input receipt ID, paper-only execution artifact and reconciliation |

Use distinct artifacts for each stage; include exact commit SHA, full
SHA-256, CI run URL, and `live_execution_enabled: false`.
Run validator tests:

```sh
python -m unittest discover -s scripts -p 'test_check_cross_module_acceptance.py' -v
python scripts/check_cross_module_acceptance.py retained-manifest.json --evidence-dir retained-artifacts/
```

The real three-host and 100-peer workflows must be dispatched separately.
Only after their outputs are retained should this manifest be assembled.
A passing unit test only verifies the validator, not a real end-to-end
Rust P2P -> ledger -> paper execution run. Do not enable live orders.
