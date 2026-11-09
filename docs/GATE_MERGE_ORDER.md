# Gate merge order and next evidence

Base of this note: `main` at `5868fb45fe6b3d85651a74caab4e4f81cf6233a7`. The checkbox inventory is `docs/INTEGRATED_DELIVERY_GATES_2026_11_08.md`. Refs #207 and #182. Live trading is not authorized. No row below is Implemented with evidence except the already evidenced action-pin ratchet (T02).

## Removed because they merged

#264, #265, #276, #277, #278, #279, #286, #293, #298, #299, #300, and #301. Merging them did not change a tracker status. #264 and #265 stay Partial or Remaining as recorded in the inventory. Delete `.github/workflows/ghost-sniffer-lock-refresh.yml`; it is SHA-pinned and only matches an already merged branch.

## Merge order for what is still open

1. **#302** (#171, T01), draft. Regenerates the Rust SQLite lock. It does not clear paste 1.0.15, so T01 stays Partial even if it merges.
2. **#281** (#171, T01). Docs only for residual #171 boundaries.
3. **#280** then **#283** (#176, T04). #283 is stacked on #280. Reconnect backoff and a per-tick dial cap. Not a 100-peer soak.
4. **#291 merged** (`5868fb45`, #176, #255). It is not Implemented. No three-peer mesh run on that commit printed `BOOTSTRAP_RECONNECT=PASS` and `THREE_PEER_MESH_AND_RECOVERY=PASS`. Next evidence: that workflow on `5868fb45` after CI succeeds, then the 100-peer independent-host soak.
5. **#284** (#180, T17) before **#271**. #278 has merged. #284 is a read-only enrolled-identity view, not live peer telemetry, and it has no CI evidence yet. #271 is the separate health-projection draft and should not land ahead of #284. T17 stays Partial.
6. **#296** and **#268** (#179, T13, T16). Extra risk tests. #277 already merged the paper kill-switch path. These do not create broker OMS evidence.
7. **#272** (#181, T18, T19). Staging config check. #300 already merged a non-mutating readiness gate. Neither is a restore drill.
8. **#270** (#177, T03, T06) after a review against the existing receipt path.
9. **#292** (#178, T07, T08). Bounded tick ingestion. Not a licensed feed.
10. **#273** and **#295** (#178, T11, T12). Eligibility function and a simulation dashboard. Not an out-of-sample strategy report.
11. **Hold #274** (#182, T03, T19) until host binding uses `measurement_class`.
12. **#290**, **#294**, and **#297** (#181, #182, T19). Manual strict mode, CI URL shape, and a one-command verifier. Not capacity or failover evidence.
13. **#288** (T03, T04, T06, T09, T14, T20). Evidence-capture templates. A template is not a passing run.
14. **#269** optional (#176, #255, T04). Equivalent classification cases already run in CI.
15. **#303** is an earlier docs note for the #284/#271 order. This file supersedes that sentence.

## Next evidence

| Step | Owner | Done when |
| --- | --- | --- |
| 100-peer independent-host soak | #176, #255 (T04) | Distinct host fingerprints, `measurement_class=operator_attested_real_multi_host`, node binary SHA-256, latency, loss, and outage recovery on the candidate commit. |
| 2/3-host E2EE with binary hashes | #176, #177 (T03) | Two-host and three-host workflows re-run on the candidate, each node binary SHA-256 retained with the run URL. |
| OMS reconciliation | #179 (T14) | Broker sandbox state matches paper orders and fills after retry and restart. #265's gate does not connect to a broker. |
| Independent crypto and risk review | #177, #179, #182 (T06, T13, T20) | Named reviewer, scope, commit, and an approved decision. |
| Feed licence signoff | #178 (T09) | Written entitlement and redistribution decision for each admitted vendor. #264 and #301 do not provide it. |
