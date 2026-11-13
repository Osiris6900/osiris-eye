# Gate merge order and next evidence

Base of this note: `main`. The checkbox inventory is [PR #275](https://github.com/Osiris6900/osiris-eye/pull/275). Refs #207 and #182. Live trading is not authorized. Merging a row below does not by itself satisfy a tracker checkbox.

## Merge order

1. **#276** (#171). Hickory/Yamux lock floor. `Cargo.lock` is unchanged. T01 stays Partial. **#281** is stacked on #276 and only edits `docs/workstreams/ISSUE-171.md`. **#286** is independent of those and teaches the SBOM to read the hashed Python lock. It does not clear paste.
2. **#279** (#255, #176). Rejection-log throttle lines. Land before a new soak. T04 stays Remaining.
3. **#280** (#176) after #279. Its git base is #279's branch. Reconnect backoff only. **#283** is stacked on #280 and only caps dials per reconnect tick.
4. **#277** (#179) has merged. Kill switch, stale data, and an internal paper reconcile. No broker order.
5. **#305** (#179, T14) follows #277. It merged as scaffolding at `deed94692cd92096d84d8657e3d12034691d8466` (opened at `fe90bae1778b7e30a17a5e991c9a585a71718b60`, head `cb83b019ab5a897938623d052458c6b6d6f706a5`). Quant API CI 42683473725 and dependency audit 42683473791 passed; main CI 42683473981 was cancelled. T14 stays Partial: no broker-sandbox match, unrealized loss is not an input to the daily-loss gate, and live execution stays disabled.
6. **#268** (#179) only if #277 is not merged. Same new test file. #277 has merged, so this row is optional.
7. **#278** (#180) has merged. Read-only fills and outbox status; `peer_count` stays null.
8. **#284** (#180) has merged after #278. The read-only enrolled-identity registry passed CI and dependency audit on its PR head. This is enrollment inventory, not live peer telemetry; T17 stays Partial.
9. **#271** (#180) follows #284. Verify its health projection against the current `admin_centre` permissions before merging.
10. **#272** (#181). Staging config check. Not a restore drill.
11. **#270** (#177) after a review against the existing receipt path.
12. **#273** (#178). Out-of-sample eligibility function. T12 stays Partial.
13. **#264** (#178). Offline feed validator. T09 stays Remaining.
14. **Hold #274** (#182) until host binding uses `measurement_class`.
15. **#265** (#179) last. Read it beside `durable_paper_ledger.py` and #277 so two paper books are not merged blindly.
16. **#269** (#176, #255) optional. Equivalent host-classification cases already run in CI.

## Next evidence

| Step | Owner | Done when |
| --- | --- | --- |
| 100-peer independent-host soak | #176, #255 (T04) | Distinct host fingerprints, `measurement_class=operator_attested_real_multi_host`, node binary SHA-256, latency, loss, and outage recovery on the candidate commit. |
| 2/3-host E2EE with binary hashes | #176, #177 (T03) | Two-host and three-host workflows re-run on the candidate, each node binary SHA-256 retained with the run URL. |
| OMS reconciliation | #179 (T14) | Broker sandbox state matches paper orders and fills after retry and restart. #305's paper lifecycle is scaffolding on the internal book. It does not match a broker sandbox, and unrealized loss is not an input to the daily-loss gate. |
| Independent crypto and risk review | #177, #179, #182 (T06, T13, T20) | Named reviewer, scope, commit, and an approved decision. This checklist is not that review. |
| Feed licence signoff | #178 (T09) | Written entitlement and redistribution decision for each admitted vendor. #264 does not provide it. |
