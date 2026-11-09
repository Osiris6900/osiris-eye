# #179 — Paper OMS + risk failure matrix (evidence contract)

This first hardening pass validates **retained evidence**; it does not
inject failures into the running OMS. Do not mark #179 accepted until the
actual paper OMS and risk gate execute the scenarios and publish their logs.

| Scenario | Failure injection | Required observation |
| --- | --- | --- |
| kill_switch | Activate independent risk kill switch before order submission | Zero accepted orders after activation; all pending orders reconciled |
| restart_recovery | Terminate paper OMS with pending order, restart and replay ledger | No duplicate orders; zero unreconciled orders |
| stale_data_rejection | Expire market-data timestamp before paper order | Order rejected; zero accepted orders after stale-feed gate |

Run local evidence-validator unit tests:

```sh
python -m unittest discover -s scripts -p 'test_check_paper_failure_matrix.py' -v
python scripts/check_paper_failure_matrix.py path/to/retained-paper-failure-evidence.json
```

The evidence JSON requires `commit_sha`, `live_execution_enabled: false`
and a `scenarios` map keyed by the three names above. Each scenario requires
`passed: true`, the same exact commit, zero
`orders_accepted_after_failure`, zero `unreconciled_orders`, the actual
`test_command`, and a GitHub Actions run URL. Do **not** populate these
fields with synthetic pass results or claim release acceptance based on
validator unit tests. This workstream is incomplete until E2E simulator
execution and retained artifacts are available.
