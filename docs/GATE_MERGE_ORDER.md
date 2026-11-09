# Gate merge order and next evidence

Base of this note: `main`. The checkbox inventory is [PR #275](https://github.com/Osiris6900/osiris-eye/pull/275). Refs #207 and #182. Live trading is not authorized. Merging a row below does not by itself satisfy a tracker checkbox.

## Merge order

1. **#276** (#171). Hickory/Yamux lock floor. `Cargo.lock` is unchanged. T01 stays Partial. **#281** is stacked on #276 and only edits `docs/workstreams/ISSUE-171.md`. **#286** is independent of those and teaches the SBOM to read the hashed Python lock. It does not clear paste.
2. **#279** (#255, #176). Rejection-log throttle lines. Land before a new soak. T04 stays Remaining.
3. **#280** (#176) after #279. Its git base is #279's branch. Reconnect backoff only. **#283** is stacked on #280 and only caps dials per reconnect tick.
4. **#277** (#179) ahead of #268. Kill switch, stale data, and an internal paper reconcile. No broker order.
5. **#268** (#179) only if #277 is not merged. Same new test file.
6. **#278** (#180) before #271. Read-only fills and outbox status. `peer_count` stays null. **#284** is stacked on #278 and adds a read-only enrolled-identity view. T17 stays Partial.
7. **#272** (#181). Staging config check. Not a restore drill.
8. **#270** (#177) after a review against the existing receipt path.
9. **#271** (#180) after #278, using the `admin_centre` permissions.
10. **#273** (#178). Out-of-sample eligibility function. T12 stays Partial.
11. **#264** (#178). Offline feed validator. T09 stays Remaining.
12. **Hold #274** (#182) until host binding uses `measurement_class`.
13. **#265** (#179) last. Read it beside `durable_paper_ledger.py` and #277 so two paper books are not merged blindly.
14. **#269** (#176, #255) optional. Equivalent host-classification cases already run in CI.

## Next evidence

| Step | Owner | Done when |
| --- | --- | --- |
| 100-peer independent-host soak | #176, #255 (T04) | Distinct host fingerprints, `measurement_class=operator_attested_real_multi_host`, node binary SHA-256, latency, loss, and outage recovery on the candidate commit. |
| 2/3-host E2EE with binary hashes | #176, #177 (T03) | Two-host and three-host workflows re-run on the candidate, each node binary SHA-256 retained with the run URL. |
| OMS reconciliation | #179 (T14) | Broker sandbox state matches paper orders and fills after retry and restart. #277's internal `reconcile()` is not that match. |
| Independent crypto and risk review | #177, #179, #182 (T06, T13, T20) | Named reviewer, scope, commit, and an approved decision. This checklist is not that review. |
| Feed licence signoff | #178 (T09) | Written entitlement and redistribution decision for each admitted vendor. #264 does not provide it. |
